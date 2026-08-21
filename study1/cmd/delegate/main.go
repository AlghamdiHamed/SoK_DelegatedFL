package main

import (
	"encoding/binary"
	"encoding/json"
	"errors"
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

func split(payload []byte) (regression.Model, []byte, error) {
	if len(payload) < 4 {
		return regression.Model{}, nil, errors.New("short request")
	}
	n := int(binary.BigEndian.Uint32(payload[:4]))
	if n < 2 || 4+n > len(payload) {
		return regression.Model{}, nil, errors.New("model length")
	}
	var m regression.Model
	if e := json.Unmarshal(payload[4:4+n], &m); e != nil {
		return m, nil, e
	}
	return m, payload[4+n:], nil
}
func rpc(addr string, m protocol.Message, l *protocol.JSONLLogger) (protocol.Message, error) {
	c, e := net.Dial("tcp", addr)
	if e != nil {
		return protocol.Message{}, e
	}
	defer c.Close()
	if _, e = protocol.Write(c, m, l); e != nil {
		return protocol.Message{}, e
	}
	r, _, e := protocol.Read(c, l)
	return r, e
}
func decodeFloats(b []byte) ([]float64, error) {
	if len(b) < 4 {
		return nil, errors.New("short floats")
	}
	n := int(binary.BigEndian.Uint32(b[:4]))
	if len(b) != 4+8*n {
		return nil, errors.New("float length")
	}
	v := make([]float64, n)
	o := 4
	for i := range v {
		v[i] = math.Float64frombits(binary.BigEndian.Uint64(b[o : o+8]))
		o += 8
	}
	return v, nil
}
func writeResource(path string, startCPU int64, startWall time.Time, m *metrics.Monitor) {
	samples := m.Stop()
	out := map[string]any{"sampled_rss": samples, "os_peak_rss_bytes": metrics.PeakRSSBytes(), "process_cpu_ns": metrics.ProcessCPUTimeNS() - startCPU, "process_wall_ns": time.Since(startWall).Nanoseconds()}
	b, _ := json.Marshal(out)
	f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if e == nil {
		_, _ = f.Write(append(b, '\n'))
		_ = f.Close()
	}
}
func exclusive(path string, b []byte) error {
	f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if e != nil {
		return e
	}
	if _, e = f.Write(b); e != nil {
		f.Close()
		return e
	}
	return f.Close()
}

func main() {
	processStart := time.Now()
	listen := flag.String("listen", "0.0.0.0:7001", "listen address")
	aggregator := flag.String("aggregator", "127.0.0.1:7002", "trusted aggregator")
	runDir := flag.String("run-dir", "", "run directory")
	runID := flag.String("run-id", "", "run ID")
	configuration := flag.String("configuration", "", "I-P, I-OR, or I-ER")
	flag.Parse()
	if *runDir == "" || *runID == "" || *configuration == "" {
		log.Fatal("--run-dir, --run-id, and --configuration required")
	}
	logger, e := protocol.NewJSONLLogger(filepath.Join(*runDir, "messages_E.jsonl"))
	if e != nil {
		log.Fatal(e)
	}
	defer logger.Close()
	trace, e := metrics.NewTrace(filepath.Join(*runDir, "operations_E.csv"), *runID, *configuration)
	if e != nil {
		log.Fatal(e)
	}
	defer trace.Close()
	startCPU, startWall := metrics.ProcessCPUTimeNS(), time.Now()
	monitor := metrics.NewMonitor(50 * time.Millisecond)
	defer writeResource(filepath.Join(*runDir, "resources_E.json"), startCPU, startWall, monitor)
	var eval *ckks.Evaluator
	if *configuration == "I-OR" || *configuration == "I-ER" {
		keyReq := protocol.Message{Header: protocol.NewHeader("E", "A", "study1", "O_1", 0, "KEY_REQUEST_EXECUTOR", nil)}
		keyReply, e := rpc(*aggregator, keyReq, logger)
		if e != nil {
			log.Fatal(e)
		}
		p, pk, rlk, gks, e := ckks.ParseEvaluationBundle(keyReply.Payload)
		if e != nil {
			log.Fatal(e)
		}
		eval = ckks.NewEvaluator(p, rlk, gks, pk)
	}
	plainData := map[string]regression.Dataset{}
	heData := map[string]ckks.EncryptedDataset{}
	var dataMu sync.RWMutex
	ln, e := net.Listen("tcp", *listen)
	if e != nil {
		log.Fatal(e)
	}
	defer ln.Close()
	cold, _ := json.Marshal(map[string]any{"boundary": "process_main_entry_to_listening_socket", "cold_start_ns": time.Since(processStart).Nanoseconds()})
	if e = exclusive(filepath.Join(*runDir, "cold_start_E.json"), append(cold, '\n')); e != nil {
		log.Fatal(e)
	}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() { <-stop; _ = ln.Close() }()
	for {
		c, e := ln.Accept()
		if e != nil {
			return
		}
		go func(conn net.Conn) {
			defer conn.Close()
			req, _, e := protocol.Read(conn, logger)
			if e != nil {
				log.Printf("read: %v", e)
				return
			}
			var result []byte
			kind := "ERROR"
			switch req.Header.MessageType {
			case "PLAIN_DATASET":
				var d regression.Dataset
				d, e = regression.UnmarshalDataset(req.Payload)
				if e == nil {
					dataMu.Lock()
					plainData[req.Header.ClientID] = d
					dataMu.Unlock()
					e = exclusive(filepath.Join(*runDir, "executor_view_plain_dataset.bin"), req.Payload)
					result = []byte("{\"status\":\"stored\"}")
					kind = "DATASET_ACCEPTED"
				}
			case "HE_DATASET":
				var d ckks.EncryptedDataset
				d, e = ckks.UnmarshalDataset(req.Payload)
				if e == nil {
					dataMu.Lock()
					heData[req.Header.ClientID] = d
					dataMu.Unlock()
					e = exclusive(filepath.Join(*runDir, "executor_view_encrypted_dataset.bin"), req.Payload)
					result = []byte("{\"status\":\"stored\"}")
					kind = "DATASET_ACCEPTED"
				}
			case "PLAIN_MODEL":
				var m regression.Model
				e = json.Unmarshal(req.Payload, &m)
				if e == nil {
					e = exclusive(filepath.Join(*runDir, fmt.Sprintf("executor_view_model_round_%02d.json", req.Header.RoundID)), append(req.Payload, '\n'))
				}
				dataMu.RLock()
				d, ok := plainData[req.Header.ClientID]
				dataMu.RUnlock()
				if !ok && e == nil {
					e = errors.New("plaintext dataset not installed")
				}
				if e == nil {
					var g regression.Gradient
					g, e = regression.FullGradient(d, m)
					if e == nil {
						payload, _ := json.Marshal(g)
						submit := protocol.Message{Header: protocol.NewHeader("E", "A", req.Header.TaskID, req.Header.ClientID, req.Header.RoundID, "PLAINTEXT_CONTRIBUTION", payload), Payload: payload}
						var accepted protocol.Message
						accepted, e = rpc(*aggregator, submit, logger)
						if e == nil && accepted.Header.MessageType == "CONTRIBUTION_ACCEPTED" {
							result = []byte(fmt.Sprintf("{\"status\":\"accepted\",\"round\":%d}", req.Header.RoundID))
							kind = "CONTRIBUTION_ACCEPTED"
						}
						_ = trace.Record(metrics.Operation{RunID: *runID, Configuration: "I-P", Round: req.Header.RoundID, Role: "E", Operation: "calculate_complete_plaintext_local_gradient", SemanticallyNecessary: true})
					}
				}
			case "HE_OR_MODEL", "HE_ER_MODEL":
				var m regression.Model
				e = json.Unmarshal(req.Payload, &m)
				if e == nil {
					e = exclusive(filepath.Join(*runDir, fmt.Sprintf("executor_view_model_round_%02d.json", req.Header.RoundID)), append(req.Payload, '\n'))
				}
				dataMu.RLock()
				d, ok := heData[req.Header.ClientID]
				dataMu.RUnlock()
				if !ok && e == nil {
					e = errors.New("encrypted dataset not installed")
				}
				if e == nil {
					var residual *rlwe.Ciphertext
					residual, e = eval.PredictionAndResiduals(d, m)
					if e == nil {
						var g ckks.EncryptedGradient
						if req.Header.MessageType == "HE_OR_MODEL" {
							rb, er := ckks.MarshalPackedCiphertext(residual, d.N)
							if er != nil {
								e = er
								break
							}
							open := protocol.Message{Header: protocol.NewHeader("E", "A", req.Header.TaskID, req.Header.ClientID, req.Header.RoundID, "OPEN_RESIDUALS", rb), Payload: rb}
							var opened protocol.Message
							opened, e = rpc(*aggregator, open, logger)
							if e == nil {
								var rv []float64
								rv, e = decodeFloats(opened.Payload)
								if e == nil {
									raw, _ := json.Marshal(rv)
									e = exclusive(filepath.Join(*runDir, fmt.Sprintf("executor_view_plaintext_residuals_round_%02d.json", req.Header.RoundID)), append(raw, '\n'))
									if e == nil {
										g, e = eval.GradientOpenedResidual(d, rv)
									}
									_ = trace.Record(metrics.Operation{RunID: *runID, Configuration: "I-OR", Round: req.Header.RoundID, Role: "E", Operation: "predict_form_residual_compute_complete_gradient", SemanticallyNecessary: true})
								}
							}
						} else {
							g, e = eval.GradientEncryptedResidual(d, residual)
							_ = trace.Record(metrics.Operation{RunID: *runID, Configuration: "I-ER", Round: req.Header.RoundID, Role: "E", Operation: "homomorphic_predict_residual_complete_gradient", SemanticallyNecessary: true})
						}
						if e == nil {
							gb, _ := ckks.MarshalGradient(g)
							submit := protocol.Message{Header: protocol.NewHeader("E", "A", req.Header.TaskID, req.Header.ClientID, req.Header.RoundID, "ENCRYPTED_GRADIENT", gb), Payload: gb}
							var accepted protocol.Message
							accepted, e = rpc(*aggregator, submit, logger)
							if e == nil && accepted.Header.MessageType == "CONTRIBUTION_ACCEPTED" {
								result = []byte(fmt.Sprintf("{\"status\":\"accepted\",\"round\":%d}", req.Header.RoundID))
								kind = "CONTRIBUTION_ACCEPTED"
							}
						}
					}
				}
			default:
				e = errors.New("unsupported request")
			}
			if e != nil {
				result = []byte(e.Error())
				kind = "ERROR"
			}
			h := protocol.NewHeader("E", req.Header.Sender, req.Header.TaskID, req.Header.ClientID, req.Header.RoundID, kind, result)
			_, _ = protocol.Write(conn, protocol.Message{Header: h, Payload: result}, logger)
		}(c)
	}
}
