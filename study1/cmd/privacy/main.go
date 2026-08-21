package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"delegatedfl/study1/internal/metrics"
	"delegatedfl/study1/internal/regression"
)

var activities = []int{1, 2, 3, 4, 5, 6, 7, 12, 13, 16, 17, 24}

type records struct {
	X        [][]float64
	Y        []float64
	Activity []int
}

func readRecords(path, partition string) (records, error) {
	f, e := os.Open(path)
	if e != nil {
		return records{}, e
	}
	defer f.Close()
	r := csv.NewReader(f)
	h, e := r.Read()
	if e != nil {
		return records{}, e
	}
	xcols := []int{}
	owner, part, act, target := -1, -1, -1, -1
	for i, n := range h {
		if strings.HasPrefix(n, "x_") {
			xcols = append(xcols, i)
		}
		switch n {
		case "owner":
			owner = i
		case "partition":
			part = i
		case "activity":
			act = i
		case "target_heart_rate":
			target = i
		}
	}
	var out records
	for {
		row, e := r.Read()
		if e != nil {
			break
		}
		if row[owner] != "O_1" || row[part] != partition {
			continue
		}
		x := make([]float64, len(xcols))
		for j, c := range xcols {
			x[j], _ = strconv.ParseFloat(row[c], 64)
		}
		y, _ := strconv.ParseFloat(row[target], 64)
		a, _ := strconv.Atoi(row[act])
		out.X = append(out.X, x)
		out.Y = append(out.Y, y)
		out.Activity = append(out.Activity, a)
	}
	return out, nil
}
func meanSD(x [][]float64) ([]float64, []float64) {
	d := len(x[0])
	m := make([]float64, d)
	s := make([]float64, d)
	for _, r := range x {
		for j, v := range r {
			m[j] += v
		}
	}
	for j := range m {
		m[j] /= float64(len(x))
	}
	for _, r := range x {
		for j, v := range r {
			z := v - m[j]
			s[j] += z * z
		}
	}
	for j := range s {
		s[j] = math.Sqrt(s[j] / float64(len(x)))
		if s[j] == 0 {
			s[j] = 1
		}
	}
	return m, s
}
func stratified(y []int) (train, val []int) {
	for _, c := range activities {
		idx := []int{}
		for i, v := range y {
			if v == c {
				idx = append(idx, i)
			}
		}
		cut := int(math.Floor(.75 * float64(len(idx))))
		if cut < 1 {
			cut = 1
		}
		if cut >= len(idx) {
			cut = len(idx) - 1
		}
		train = append(train, idx[:cut]...)
		val = append(val, idx[cut:]...)
	}
	return
}
func subsetX(x [][]float64, idx []int) [][]float64 {
	out := make([][]float64, len(idx))
	for i, j := range idx {
		out[i] = x[j]
	}
	return out
}
func subsetY(y []int, idx []int) []int {
	out := make([]int, len(idx))
	for i, j := range idx {
		out[i] = y[j]
	}
	return out
}
func tune(aux records, seed int) (float64, error) {
	tr, va := stratified(aux.Activity)
	best, bestScore := .01, -1.0
	for _, C := range []float64{.01, .1, 1, 10} {
		m, e := metrics.FitProbe(subsetX(aux.X, tr), subsetY(aux.Activity, tr), activities, C)
		if e != nil {
			return 0, e
		}
		q := metrics.ScoreClassification(subsetY(aux.Activity, va), m.Predict(subsetX(aux.X, va)), activities)
		if q.BalancedAccuracy > bestScore {
			best, bestScore = C, q.BalancedAccuracy
		}
	}
	return best, nil
}
func residuals(path string) ([]float64, error) {
	var x []float64
	b, e := os.ReadFile(path)
	if e != nil {
		return nil, e
	}
	e = json.Unmarshal(b, &x)
	return x, e
}
func perm(n, round int, condition string, seed int) []int {
	p := make([]int, n)
	for i := range p {
		p[i] = i
	}
	if condition == "stable_correct" {
		return p
	}
	fraction := 0.0
	switch condition {
	case "correct_25pct":
		fraction = .25
	case "correct_50pct":
		fraction = .5
	case "correct_75pct":
		fraction = .75
	}
	k := int(fraction * float64(n))
	if condition == "shuffled_each_round" {
		return rand.New(rand.NewSource(int64(seed + round*1009))).Perm(n)
	}
	start := k
	if condition == "deliberately_permuted" {
		start = 0
	}
	if n-start > 1 {
		tail := append([]int(nil), p[start:]...)
		shift := 1 + (round % (len(tail) - 1))
		for i := range tail {
			p[start+i] = tail[(i+shift)%len(tail)]
		}
	}
	return p
}
func fs(v float64) string { return strconv.FormatFloat(v, 'g', 17, 64) }
func main() {
	recordsPath := flag.String("records", "", "study1_records.csv")
	runDir := flag.String("run-dir", "", "I-OR raw run directory")
	runID := flag.String("run-id", "", "run ID")
	seed := flag.Int("seed", 0, "learning seed")
	output := flag.String("output", "", "study1_privacy.csv")
	linkOutput := flag.String("linkage-output", "", "study1_linkage.csv")
	flag.Parse()
	if *recordsPath == "" || *runDir == "" || *runID == "" || *output == "" || *linkOutput == "" {
		panic("missing flags")
	}
	target, e := readRecords(*recordsPath, "learning")
	if e != nil {
		panic(e)
	}
	aux, e := readRecords(*recordsPath, "attacker_auxiliary")
	if e != nil {
		panic(e)
	}
	// Feasibility froze one O_1 auxiliary record per activity, so an
	// auxiliary-only stratified train/validation split cannot contain every
	// class on both sides. Study I did not prescribe C tuning. C=1 was frozen
	// before any reconstruction/probe output was produced and is shared by all
	// seeds; the auxiliary records remain wholly separate from target records.
	const C = 1.0
	probe, e := metrics.FitProbe(aux.X, aux.Activity, activities, C)
	if e != nil {
		panic(e)
	}
	neg, e := metrics.FitProbe(aux.X, metrics.ShuffleLabels(aux.Activity, int64(*seed+100000)), activities, C)
	if e != nil {
		panic(e)
	}
	mean, sd := meanSD(aux.X)
	models := make([]regression.Model, 10)
	roundResidual := make([][]float64, 10)
	for r := 1; r <= 10; r++ {
		b, e := os.ReadFile(filepath.Join(*runDir, fmt.Sprintf("executor_view_model_round_%02d.json", r)))
		if e != nil {
			panic(e)
		}
		if e = json.Unmarshal(b, &models[r-1]); e != nil {
			panic(e)
		}
		roundResidual[r-1], e = residuals(filepath.Join(*runDir, fmt.Sprintf("executor_view_plaintext_residuals_round_%02d.json", r)))
		if e != nil {
			panic(e)
		}
		if len(roundResidual[r-1]) != len(target.X) {
			panic("residual/sample count")
		}
	}
	f, e := os.OpenFile(*output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if e != nil {
		panic(e)
	}
	defer f.Close()
	w := csv.NewWriter(f)
	defer w.Flush()
	header := strings.Split("run_id,seed,configuration,R,equations,rank,singular_values,condition_number,solver,regularization,rank_tolerance,linkage,heart_rate_mae,heart_rate_rmse,heart_rate_r2,feature_standardized_rmse,feature_relative_l2,feature_cosine,activity_balanced_accuracy,activity_macro_f1,reference_accuracy,shuffled_balanced_accuracy,shuffled_macro_f1,metadata_only_balanced_accuracy,metadata_only_macro_f1,attack_restart", ",")
	_ = w.Write(header)
	lf, e := os.OpenFile(*linkOutput, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if e != nil {
		panic(e)
	}
	defer lf.Close()
	lw := csv.NewWriter(lf)
	defer lw.Flush()
	_ = lw.Write(strings.Split("run_id,seed,R,linkage,correct_linkage_fraction,equations,rank,condition_number,heart_rate_mae,feature_standardized_rmse,activity_balanced_accuracy", ","))
	for _, condition := range []string{"stable_correct", "shuffled_each_round", "correct_25pct", "correct_50pct", "correct_75pct", "deliberately_permuted"} {
		for _, R := range []int{1, 5, 10} {
			predX := make([][]float64, len(target.X))
			predY := make([]float64, len(target.Y))
			var diag metrics.ReconstructionDiagnostics
			maps := make([][]int, R)
			for r := 0; r < R; r++ {
				maps[r] = perm(len(target.X), r+1, condition, *seed)
			}
			for n := range target.X {
				obs := make([]float64, R)
				for r := 0; r < R; r++ {
					obs[r] = roundResidual[r][maps[r][n]]
				}
				rec, e := metrics.Reconstruct(models[:R], obs)
				if e != nil {
					panic(e)
				}
				diag = rec.Diagnostics
				predX[n] = append([]float64(nil), rec.Values[:regression.Dimension]...)
				predY[n] = rec.Values[regression.Dimension]
			}
			cm := metrics.Continuous(target.Y, predY)
			fm := metrics.Features(target.X, predX, mean, sd)
			score := metrics.ScoreClassification(target.Activity, probe.Predict(predX), activities)
			negative := metrics.ScoreClassification(target.Activity, neg.Predict(predX), activities)
			sv, _ := json.Marshal(diag.SingularValues)
			row := []string{*runID, strconv.Itoa(*seed), "I-OR", strconv.Itoa(R), strconv.Itoa(diag.Equations), strconv.Itoa(diag.Rank), string(sv), fs(diag.ConditionNumber), diag.Solver, "0", fs(diag.RankTolerance), condition, fs(cm.MAE), fs(cm.RMSE), fs(cm.R2), fs(fm.StandardizedRMSE), fs(fm.RelativeL2), fs(fm.Cosine), fs(score.BalancedAccuracy), fs(score.MacroF1), fs(1.0 / 12), fs(negative.BalancedAccuracy), fs(negative.MacroF1), "", "", ""}
			_ = w.Write(row)
			fraction := map[string]float64{"stable_correct": 1, "shuffled_each_round": 0, "correct_25pct": .25, "correct_50pct": .5, "correct_75pct": .75, "deliberately_permuted": 0}[condition]
			_ = lw.Write([]string{*runID, strconv.Itoa(*seed), strconv.Itoa(R), condition, fs(fraction), strconv.Itoa(diag.Equations), strconv.Itoa(diag.Rank), fs(diag.ConditionNumber), fs(cm.MAE), fs(fm.StandardizedRMSE), fs(score.BalancedAccuracy)})
		}
	}
}
