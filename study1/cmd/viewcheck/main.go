package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func main() {
	runDir := flag.String("run-dir", "", "I-ER raw run")
	records := flag.String("records", "", "study1_records.csv")
	runID := flag.String("run-id", "", "run ID")
	seed := flag.Int("seed", 0, "seed")
	output := flag.String("output", "", "study1_privacy.csv")
	flag.Parse()
	if *runDir == "" || *records == "" || *output == "" {
		panic("missing flags")
	}
	if hits, _ := filepath.Glob(filepath.Join(*runDir, "executor_view_plaintext_residuals_*.json")); len(hits) > 0 {
		panic("I-ER contains plaintext residual artifact")
	}
	b, e := os.ReadFile(filepath.Join(*runDir, "messages_E.jsonl"))
	if e != nil {
		panic(e)
	}
	if strings.Contains(string(b), "PLAINTEXT_RESIDUALS") {
		panic("I-ER executor trace contains plaintext residual message")
	}
	f, e := os.Open(*records)
	if e != nil {
		panic(e)
	}
	r := csv.NewReader(f)
	h, _ := r.Read()
	owner, part, activity := -1, -1, -1
	for i, n := range h {
		switch n {
		case "owner":
			owner = i
		case "partition":
			part = i
		case "activity":
			activity = i
		}
	}
	support := map[string]int{}
	for {
		row, e := r.Read()
		if e != nil {
			break
		}
		if row[owner] == "O_1" && row[part] == "learning" {
			support[row[activity]]++
		}
	}
	f.Close()
	majority := ""
	for c, n := range support {
		if majority == "" || n > support[majority] || n == support[majority] && c < majority {
			majority = c
		}
	}
	tp := support[majority]
	total := 0
	for _, n := range support {
		total += n
	}
	precision := float64(tp) / float64(total)
	macroF1 := (2 * precision / (1 + precision)) / 12
	out, e := os.OpenFile(*output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if e != nil {
		panic(e)
	}
	defer out.Close()
	w := csv.NewWriter(out)
	defer w.Flush()
	_ = w.Write(strings.Split("run_id,seed,configuration,R,equations,rank,singular_values,condition_number,solver,regularization,rank_tolerance,linkage,heart_rate_mae,heart_rate_rmse,heart_rate_r2,feature_standardized_rmse,feature_relative_l2,feature_cosine,activity_balanced_accuracy,activity_macro_f1,reference_accuracy,shuffled_balanced_accuracy,shuffled_macro_f1,metadata_only_balanced_accuracy,metadata_only_macro_f1,attack_restart", ","))
	row := make([]string, 26)
	row[0] = *runID
	row[1] = strconv.Itoa(*seed)
	row[2] = "I-ER"
	row[23] = fmt.Sprintf("%.17g", 1.0/12)
	row[24] = fmt.Sprintf("%.17g", macroF1)
	_ = w.Write(row)
}
