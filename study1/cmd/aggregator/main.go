package main

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"time"

	"delegatedfl/study1/internal/ckks"
	"delegatedfl/study1/internal/metrics"
	"delegatedfl/study1/internal/protocol"
	"delegatedfl/study1/internal/regression"

	"github.com/tuneinsight/lattigo/v5/core/rlwe"
)

func floatPayload(v []float64) []byte {
	b := make([]byte, 4+8*len(v))
	binary.BigEndian.PutUint32(b[:4], uint32(len(v)))

	o := 4
	for _, x := range v {
		binary.BigEndian.PutUint64(
			b[o:o+8],
			mathFloatBits(x),
		)
		o += 8
	}

	return b
}

func mathFloatBits(v float64) uint64 {
	return math.Float64bits(v)
}

func exclusive(path string, b []byte) error {
	f, e := os.OpenFile(
		path,
		os.O_WRONLY|os.O_CREATE|os.O_EXCL,
		0o600,
	)
	if e != nil {
		return e
	}

	if _, e = f.Write(b); e != nil {
		f.Close()
		return e
	}

	return f.Close()
}

func writeResource(
	path string,
	startCPU int64,
	startWall time.Time,
	m *metrics.Monitor,
) {
	samples := m.Stop()

	out := map[string]any{
		"sampled_rss":       samples,
		"os_peak_rss_bytes": metrics.PeakRSSBytes(),
		"process_cpu_ns":    metrics.ProcessCPUTimeNS() - startCPU,
		"process_wall_ns":   time.Since(startWall).Nanoseconds(),
	}

	b, _ := json.Marshal(out)
	_ = exclusive(path, append(b, '\n'))
}

func writeKeyInventory(runDir, keyState string) error {
	roles := map[string][]string{
		"secret.key":          {"A"},
		"public.key":          {"A", "O_1", "E"},
		"relinearization.key": {"A", "E"},
		"rotation.keys":       {"A", "E"},
		"parameters.bin":      {"A", "O_1", "E"},
	}

	files := map[string]any{}

	for name, possessors := range roles {
		b, e := os.ReadFile(
			filepath.Join(keyState, name),
		)
		if e != nil {
			return e
		}

		h := sha256.Sum256(b)

		files[name] = map[string]any{
			"bytes":            len(b),
			"sha256":           fmt.Sprintf("%x", h[:]),
			"possessing_roles": possessors,
		}
	}

	raw, _ := json.Marshal(
		map[string]any{
			"files":               files,
			"secret_key_leaves_A": false,
		},
	)

	return exclusive(
		filepath.Join(
			runDir,
			"study1_key_inventory.json",
		),
		append(raw, '\n'),
	)
}

type coordinator struct {
	mu        sync.Mutex
	cond      *sync.Cond
	round     int
	model     regression.Model
	gradients map[string]regression.Gradient
	runDir    string
	dispatch  map[int]time.Time
}

func newCoordinator(runDir string) *coordinator {
	c := &coordinator{
		round:     1,
		gradients: map[string]regression.Gradient{},
		runDir:    runDir,
		dispatch:  map[int]time.Time{},
	}

	c.cond = sync.NewCond(&c.mu)

	return c
}

func (c *coordinator) get(
	round int,
) (regression.Model, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	for round > c.round {
		c.cond.Wait()
	}

	if round != c.round {
		return regression.Model{}, fmt.Errorf(
			"stale/future round %d current %d",
			round,
			c.round,
		)
	}

	if _, ok := c.dispatch[round]; !ok {
		c.dispatch[round] = time.Now()
	}

	return c.model, nil
}

func (c *coordinator) add(
	round int,
	client string,
	g regression.Gradient,
) (bool, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if round != c.round {
		return false, fmt.Errorf(
			"contribution round %d current %d",
			round,
			c.round,
		)
	}

	if _, ok := c.gradients[client]; ok {
		return false, fmt.Errorf(
			"duplicate contribution %s",
			client,
		)
	}

	c.gradients[client] = g

	if len(c.gradients) < 3 {
		return false, nil
	}

	ordered := []regression.Gradient{
		c.gradients["O_1"],
		c.gradients["O_2"],
		c.gradients["O_3"],
	}

	for _, x := range ordered {
		if x.N <= 0 {
			return false, fmt.Errorf(
				"missing owner contribution",
			)
		}
	}

	agg, e := regression.Aggregate(ordered)
	if e != nil {
		return false, e
	}

	c.model = regression.Update(
		c.model,
		agg,
		regression.LearningRate,
	)

	endToEnd := time.Since(
		c.dispatch[c.round],
	).Nanoseconds()

	b, _ := json.Marshal(c.model)

	if e = exclusive(
		filepath.Join(
			c.runDir,
			fmt.Sprintf(
				"global_model_round_%02d.json",
				c.round,
			),
		),
		append(b, '\n'),
	); e != nil {
		return false, e
	}

	timing, _ := json.Marshal(
		map[string]any{
			"round_id":            c.round,
			"end_to_end_round_ns": endToEnd,
		},
	)

	if e = exclusive(
		filepath.Join(
			c.runDir,
			fmt.Sprintf(
				"aggregator_round_%02d_timing.json",
				c.round,
			),
		),
		append(timing, '\n'),
	); e != nil {
		return false, e
	}

	c.round++
	c.gradients = map[string]regression.Gradient{}
	c.cond.Broadcast()

	return true, nil
}

func main() {
	processStart := time.Now()

	listen := flag.String(
		"listen",
		"0.0.0.0:7002",
		"TCP listen address",
	)
	runDir := flag.String(
		"run-dir",
		"",
		"preallocated immutable run directory",
	)
	runID := flag.String(
		"run-id",
		"",
		"run ID",
	)
	configuration := flag.String(
		"configuration",
		"",
		"I-L, I-P, I-OR, or I-ER",
	)
	keyState := flag.String(
		"key-state",
		"",
		"frozen A-only key-state directory for I-OR/I-ER",
	)

	flag.Parse()

	if *runDir == "" ||
		*runID == "" ||
		*configuration == "" {
		log.Fatal(
			"--run-dir, --run-id, and --configuration required",
		)
	}

	var authority *ckks.Authority
	var e error

	if *configuration == "I-OR" ||
		*configuration == "I-ER" {
		if *keyState == "" {
			log.Fatal(
				"HE configurations require --key-state generated once on A",
			)
		}

		authority, e = ckks.LoadAuthority(
			*keyState,
		)
		if e != nil {
			log.Fatal(e)
		}

		if e = writeKeyInventory(
			*runDir,
			*keyState,
		); e != nil {
			log.Fatal(e)
		}
	}

	logger, e := protocol.NewJSONLLogger(
		filepath.Join(
			*runDir,
			"messages_A.jsonl",
		),
	)
	if e != nil {
		log.Fatal(e)
	}
	defer logger.Close()

	trace, e := metrics.NewTrace(
		filepath.Join(
			*runDir,
			"operations_A.csv",
		),
		*runID,
		*configuration,
	)
	if e != nil {
		log.Fatal(e)
	}
	defer trace.Close()

	startCPU, startWall := metrics.ProcessCPUTimeNS(), time.Now()

	monitor := metrics.NewMonitor(
		50 * time.Millisecond,
	)

	defer writeResource(
		filepath.Join(
			*runDir,
			"resources_A.json",
		),
		startCPU,
		startWall,
		monitor,
	)

	coord := newCoordinator(*runDir)

	ln, e := net.Listen(
		"tcp",
		*listen,
	)
	if e != nil {
		log.Fatal(e)
	}
	defer ln.Close()

	cold, _ := json.Marshal(
		map[string]any{
			"boundary": "process_main_entry_to_listening_socket",
			"cold_start_ns": time.Since(
				processStart,
			).Nanoseconds(),
		},
	)

	if e = exclusive(
		filepath.Join(
			*runDir,
			"cold_start_A.json",
		),
		append(cold, '\n'),
	); e != nil {
		log.Fatal(e)
	}

	log.Printf(
		"Study-I trusted aggregator/decryption service on %s",
		*listen,
	)

	stop := make(chan os.Signal, 1)

	signal.Notify(
		stop,
		os.Interrupt,
		syscall.SIGTERM,
	)

	go func() {
		<-stop
		_ = ln.Close()
	}()

	for {
		c, e := ln.Accept()
		if e != nil {
			return
		}

		go func(conn net.Conn) {
			defer conn.Close()

			m, _, e := protocol.Read(
				conn,
				logger,
			)
			if e != nil {
				log.Printf(
					"read: %v",
					e,
				)
				return
			}

			var payload []byte
			kind := "ERROR"

			switch m.Header.MessageType {
			case "KEY_REQUEST_OWNER":
				if authority == nil {
					e = fmt.Errorf(
						"CKKS disabled for %s",
						*configuration,
					)
				} else {
					payload, e = authority.PublicBundle()
					kind = "PUBLIC_KEY_BUNDLE"
				}

			case "KEY_REQUEST_EXECUTOR":
				if authority == nil {
					e = fmt.Errorf(
						"CKKS disabled for %s",
						*configuration,
					)
				} else {
					payload, e = authority.EvaluationBundle()
					kind = "EVALUATION_KEY_BUNDLE"
				}

			case "GET_MODEL":
				var model regression.Model

				model, e = coord.get(
					m.Header.RoundID,
				)
				if e == nil {
					payload, e = json.Marshal(model)
					kind = "GLOBAL_MODEL"
				}

			case "PLAINTEXT_CONTRIBUTION":
				var g regression.Gradient

				e = json.Unmarshal(
					m.Payload,
					&g,
				)
				if e == nil {
					_, e = coord.add(
						m.Header.RoundID,
						m.Header.ClientID,
						g,
					)

					if e == nil {
						payload = []byte(
							"{\"status\":\"accepted\"}",
						)
						kind = "CONTRIBUTION_ACCEPTED"

						_ = trace.Record(
							metrics.Operation{
								RunID:                 *runID,
								Configuration:         "matched",
								Round:                 m.Header.RoundID,
								Role:                  "A",
								Operation:             "sample_weighted_gradient_aggregation_and_global_update_when_complete",
								SemanticallyNecessary: true,
							},
						)
					}
				}

			case "OPEN_RESIDUALS":
				var ct *rlwe.Ciphertext
				var n int

				ct, n, e = ckks.UnmarshalPackedCiphertext(
					m.Payload,
				)
				if e == nil {
					dec := ckks.NewDecryptor(
						authority.Params,
						authority.SK,
					)

					var v []float64

					v, e = dec.Vector(ct, n)
					payload = floatPayload(v)
					kind = "PLAINTEXT_RESIDUALS"

					_ = trace.Record(
						metrics.Operation{
							RunID:                 *runID,
							Configuration:         "I-OR",
							Round:                 m.Header.RoundID,
							Role:                  "A",
							Operation:             "decrypt_residual_only",
							SemanticallyNecessary: false,
						},
					)
				}

			case "ENCRYPTED_GRADIENT":
				var g ckks.EncryptedGradient

				g, e = ckks.UnmarshalGradient(
					m.Payload,
				)
				if e == nil {
					plain, er := ckks.NewDecryptor(
						authority.Params,
						authority.SK,
					).Gradient(g)

					e = er

					if e == nil {
						raw, _ := json.Marshal(plain)

						name := fmt.Sprintf(
							"decrypted_gradient_%s_round_%02d.json",
							m.Header.ClientID,
							m.Header.RoundID,
						)

						e = exclusive(
							filepath.Join(
								*runDir,
								name,
							),
							append(raw, '\n'),
						)

						if e == nil {
							_, e = coord.add(
								m.Header.RoundID,
								m.Header.ClientID,
								plain,
							)
						}

						if e == nil {
							payload = []byte(
								"{\"status\":\"accepted\"}",
							)
							kind = "CONTRIBUTION_ACCEPTED"
						}

						_ = trace.Record(
							metrics.Operation{
								RunID:                 *runID,
								Configuration:         "I-HE",
								Round:                 m.Header.RoundID,
								Role:                  "A",
								Operation:             "decrypt_final_local_gradient",
								SemanticallyNecessary: false,
							},
						)
					}
				}

			default:
				e = fmt.Errorf(
					"unsupported message %s",
					m.Header.MessageType,
				)
			}

			if e != nil {
				payload = []byte(e.Error())
				kind = "ERROR"
			}

			h := protocol.NewHeader(
				"A",
				m.Header.Sender,
				m.Header.TaskID,
				m.Header.ClientID,
				m.Header.RoundID,
				kind,
				payload,
			)

			_, _ = protocol.Write(
				conn,
				protocol.Message{
					Header:  h,
					Payload: payload,
				},
				logger,
			)
		}(c)
	}
}
