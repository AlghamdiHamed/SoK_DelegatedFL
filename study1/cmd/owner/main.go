package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"log"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"delegatedfl/study1/internal/ckks"
	"delegatedfl/study1/internal/metrics"
	"delegatedfl/study1/internal/protocol"
	"delegatedfl/study1/internal/regression"
)

func rpc(addr string, m protocol.Message, l *protocol.JSONLLogger) (protocol.Message, error) {
	c, e := net.DialTimeout("tcp", addr, 10*time.Second)
	if e != nil {
		return protocol.Message{}, e
	}
	defer c.Close()
	if tcp, ok := c.(*net.TCPConn); ok {
		_ = tcp.SetNoDelay(true)
	}
	if _, e = protocol.Write(c, m, l); e != nil {
		return protocol.Message{}, e
	}
	r, _, e := protocol.Read(c, l)
	return r, e
}
func call(addr, sender, receiver, task, client string, round int, kind string, payload []byte, l *protocol.JSONLLogger) (protocol.Message, error) {
	h := protocol.NewHeader(sender, receiver, task, client, round, kind, payload)
	return rpc(addr, protocol.Message{Header: h, Payload: payload}, l)
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

func main() {
	processStart := time.Now()
	configuration := flag.String("configuration", "", "I-L, I-P, I-OR, or I-ER")
	owner := flag.String("owner", "O_1", "O_1, O_2, or O_3")
	records := flag.String("records", "", "frozen study1_records.csv")
	executor := flag.String("executor", "127.0.0.1:7001", "executor address")
	aggregator := flag.String("aggregator", "127.0.0.1:7002", "aggregator address")
	runDir := flag.String("run-dir", "", "preallocated run directory")
	runID := flag.String("run-id", "", "run ID")
	task := flag.String("task-id", "study1", "task ID")
	flag.Parse()
	if *configuration == "" || *records == "" || *runDir == "" || *runID == "" {
		log.Fatal("required flags missing")
	}
	if *owner != "O_1" && *owner != "O_2" && *owner != "O_3" {
		log.Fatal("invalid owner")
	}
	d, e := regression.ReadFrozenCSV(*records, *owner, "learning")
	if e != nil {
		log.Fatal(e)
	}
	logger, e := protocol.NewJSONLLogger(filepath.Join(*runDir, "messages_"+*owner+".jsonl"))
	if e != nil {
		log.Fatal(e)
	}
	defer logger.Close()
	trace, e := metrics.NewTrace(filepath.Join(*runDir, "operations_"+*owner+".csv"), *runID, *configuration)
	if e != nil {
		log.Fatal(e)
	}
	defer trace.Close()
	startCPU, startWall := metrics.ProcessCPUTimeNS(), time.Now()
	monitor := metrics.NewMonitor(50 * time.Millisecond)
	defer writeResource(filepath.Join(*runDir, "resources_"+*owner+".json"), startCPU, startWall, monitor)
	roundFile, e := os.OpenFile(filepath.Join(*runDir, "owner_"+*owner+"_rounds.csv"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if e != nil {
		log.Fatal(e)
	}
	defer roundFile.Close()
	roundCSV := csv.NewWriter(roundFile)
	defer roundCSV.Flush()
	_ = roundCSV.Write([]string{"round_id", "owner_active_cpu_ns", "owner_protocol_online_ns"})
	coldJSON, _ := json.Marshal(map[string]any{"boundary": "process_main_entry_to_owner_protocol_ready", "cold_start_ns": time.Since(processStart).Nanoseconds()})
	if e = exclusiveWrite(filepath.Join(*runDir, "cold_start_"+*owner+".json"), append(coldJSON, '\n')); e != nil {
		log.Fatal(e)
	}
	delegated := *owner == "O_1" && *configuration != "I-L"
	setupStart := time.Now()
	if delegated && *configuration == "I-P" {
		plain, e := regression.MarshalDataset(d)
		if e != nil {
			log.Fatal(e)
		}
		reply, e := call(*executor, *owner, "E", *task, *owner, 0, "PLAIN_DATASET", plain, logger)
		if e != nil || reply.Header.MessageType != "DATASET_ACCEPTED" {
			log.Fatalf("plain delegation setup: %v %s", e, reply.Payload)
		}
		_ = trace.Record(metrics.Operation{RunID: *runID, Configuration: *configuration, Round: 0, Role: *owner, Operation: "authorize_serialize_send_plaintext_records", SemanticallyNecessary: false})
	}
	if delegated && (*configuration == "I-OR" || *configuration == "I-ER") {
		keyReply, e := call(*aggregator, *owner, "A", *task, *owner, 0, "KEY_REQUEST_OWNER", nil, logger)
		if e != nil {
			log.Fatal(e)
		}
		p, pk, e := ckks.ParsePublicBundle(keyReply.Payload)
		if e != nil {
			log.Fatal(e)
		}
		ed, e := ckks.NewEncryptor(p, pk).Dataset(d)
		if e != nil {
			log.Fatal(e)
		}
		cipher, e := ckks.MarshalDataset(ed)
		if e != nil {
			log.Fatal(e)
		}
		reply, e := call(*executor, *owner, "E", *task, *owner, 0, "HE_DATASET", cipher, logger)
		if e != nil || reply.Header.MessageType != "DATASET_ACCEPTED" {
			log.Fatalf("HE delegation setup: %v %s", e, reply.Payload)
		}
		_ = trace.Record(metrics.Operation{RunID: *runID, Configuration: *configuration, Round: 0, Role: *owner, Operation: "authorize_encrypt_serialize_send_records_and_target", SemanticallyNecessary: false})
	}
	setupNS := time.Since(setupStart).Nanoseconds()
	setupJSON, _ := json.Marshal(map[string]any{"encryption_and_delegation_setup_ns": setupNS})
	if e = exclusiveWrite(filepath.Join(*runDir, "owner_"+*owner+"_setup.json"), append(setupJSON, '\n')); e != nil {
		log.Fatal(e)
	}
	for round := 1; round <= 10; round++ {
		roundWall, roundCPU := time.Now(), metrics.ProcessCPUTimeNS()
		modelReply, e := call(*aggregator, *owner, "A", *task, *owner, round, "GET_MODEL", nil, logger)
		if e != nil {
			log.Fatal(e)
		}
		if modelReply.Header.MessageType != "GLOBAL_MODEL" {
			log.Fatalf("model: %s", modelReply.Payload)
		}
		var model regression.Model
		if e = json.Unmarshal(modelReply.Payload, &model); e != nil {
			log.Fatal(e)
		}
		if !delegated {
			g, e := regression.FullGradient(d, model)
			if e != nil {
				log.Fatal(e)
			}
			_ = trace.Record(metrics.Operation{RunID: *runID, Configuration: *configuration, Round: round, Role: *owner, Operation: "calculate_complete_plaintext_local_gradient", SemanticallyNecessary: true})
			payload, _ := json.Marshal(g)
			reply, e := call(*aggregator, *owner, "A", *task, *owner, round, "PLAINTEXT_CONTRIBUTION", payload, logger)
			if e != nil || reply.Header.MessageType != "CONTRIBUTION_ACCEPTED" {
				log.Fatalf("contribution: %v %s", e, reply.Payload)
			}
			_ = roundCSV.Write([]string{strconv.Itoa(round), strconv.FormatInt(metrics.ProcessCPUTimeNS()-roundCPU, 10), strconv.FormatInt(time.Since(roundWall).Nanoseconds(), 10)})
			roundCSV.Flush()
			continue
		}
		payload, _ := json.Marshal(model)
		kind := "PLAIN_MODEL"
		if *configuration == "I-OR" {
			kind = "HE_OR_MODEL"
		} else if *configuration == "I-ER" {
			kind = "HE_ER_MODEL"
		}
		reply, e := call(*executor, *owner, "E", *task, *owner, round, kind, payload, logger)
		if e != nil || reply.Header.MessageType != "CONTRIBUTION_ACCEPTED" {
			log.Fatalf("delegate: %v %s", e, reply.Payload)
		}
		_ = trace.Record(metrics.Operation{RunID: *runID, Configuration: *configuration, Round: round, Role: *owner, Operation: "receive_delegated_result_status", SemanticallyNecessary: false})
		_ = roundCSV.Write([]string{strconv.Itoa(round), strconv.FormatInt(metrics.ProcessCPUTimeNS()-roundCPU, 10), strconv.FormatInt(time.Since(roundWall).Nanoseconds(), 10)})
		roundCSV.Flush()
	}
}

func exclusiveWrite(path string, b []byte) error {
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
