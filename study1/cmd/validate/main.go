package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"

	"delegatedfl/study1/internal/regression"
)

func readJSON(path string, v any) error {
	b, e := os.ReadFile(path)
	if e != nil {
		return e
	}
	return json.Unmarshal(b, v)
}
func flatGradient(g regression.Gradient) []float64 {
	x := append([]float64(nil), g.W[:]...)
	return append(x, g.B)
}
func flatModel(m regression.Model) []float64 {
	x := append([]float64(nil), m.W[:]...)
	return append(x, m.B)
}
func l2(x []float64) float64 {
	var s float64
	for _, v := range x {
		s += v * v
	}
	return math.Sqrt(s)
}
func difference(a, b []float64) (maxabs, relative float64) {
	d := make([]float64, len(a))
	for i := range a {
		d[i] = a[i] - b[i]
		if math.Abs(d[i]) > maxabs {
			maxabs = math.Abs(d[i])
		}
	}
	den := l2(b)
	if den == 0 {
		relative = math.Inf(1)
		if l2(d) == 0 {
			relative = 0
		}
	} else {
		relative = l2(d) / den
	}
	return
}
func combinedLoss(ds []regression.Dataset, m regression.Model) float64 {
	var weighted float64
	var n int
	for _, d := range ds {
		x, _ := regression.HalfMSE(d, m)
		weighted += x * float64(len(d.X))
		n += len(d.X)
	}
	return weighted / float64(n)
}
func rmse(d regression.Dataset, m regression.Model) float64 {
	e, _ := regression.Residuals(d, m)
	var s float64
	for _, x := range e {
		s += x * x
	}
	return math.Sqrt(s / float64(len(e)))
}
func f(v float64) string { return strconv.FormatFloat(v, 'g', 17, 64) }

func main() {
	records := flag.String("records", "", "study1_records.csv")
	runDir := flag.String("run-dir", "", "immutable raw run directory")
	runID := flag.String("run-id", "", "run ID")
	seed := flag.Int("seed", 0, "learning seed")
	configuration := flag.String("configuration", "", "I-OR or I-ER")
	output := flag.String("output", "", "exclusive correctness CSV")
	flag.Parse()
	if *records == "" || *runDir == "" || *runID == "" || *output == "" || (*configuration != "I-OR" && *configuration != "I-ER") {
		panic("required/invalid flags")
	}
	learning := make([]regression.Dataset, 3)
	utility := make([]regression.Dataset, 3)
	for i, owner := range []string{"O_1", "O_2", "O_3"} {
		var e error
		learning[i], e = regression.ReadFrozenCSV(*records, owner, "learning")
		if e != nil {
			panic(e)
		}
		utility[i], e = regression.ReadFrozenCSV(*records, owner, "utility_test")
		if e != nil {
			panic(e)
		}
	}
	baseline := regression.Model{}
	baselineModels := make([]regression.Model, 10)
	for r := 0; r < 10; r++ {
		gs := make([]regression.Gradient, 3)
		for i := range learning {
			gs[i], _ = regression.FullGradient(learning[i], baseline)
		}
		g, _ := regression.Aggregate(gs)
		baseline = regression.Update(baseline, g, regression.LearningRate)
		baselineModels[r] = baseline
	}
	fout, e := os.OpenFile(*output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if e != nil {
		panic(e)
	}
	defer fout.Close()
	w := csv.NewWriter(fout)
	defer w.Flush()
	_ = w.Write([]string{"run_id", "seed", "configuration", "round_id", "max_absolute_gradient_error", "relative_l2_gradient_error", "model_parameter_deviation", "training_loss_difference", "final_rmse_difference", "gate_pass"})
	for round := 1; round <= 10; round++ {
		var current regression.Model
		if e = readJSON(filepath.Join(*runDir, fmt.Sprintf("executor_view_model_round_%02d.json", round)), &current); e != nil {
			panic(e)
		}
		exact, _ := regression.FullGradient(learning[0], current)
		var observed regression.Gradient
		if e = readJSON(filepath.Join(*runDir, fmt.Sprintf("decrypted_gradient_O_1_round_%02d.json", round)), &observed); e != nil {
			panic(e)
		}
		maxerr, rel := difference(flatGradient(observed), flatGradient(exact))
		var actual regression.Model
		if e = readJSON(filepath.Join(*runDir, fmt.Sprintf("global_model_round_%02d.json", round)), &actual); e != nil {
			panic(e)
		}
		_, dev := difference(flatModel(actual), flatModel(baselineModels[round-1]))
		lossdiff := math.Abs(combinedLoss(learning, actual) - combinedLoss(learning, baselineModels[round-1]))
		finalDiff := 0.0
		if round == 10 {
			var sumA, sumB float64
			var n int
			for i := range utility {
				sumA += rmse(utility[i], actual) * float64(len(utility[i].X))
				sumB += rmse(utility[i], baselineModels[9]) * float64(len(utility[i].X))
				n += len(utility[i].X)
			}
			finalDiff = math.Abs(sumA/float64(n) - sumB/float64(n))
		}
		pass := maxerr <= 1e-4 && rel <= 1e-4 && dev <= 2e-3 && lossdiff <= 1e-4 && (round != 10 || finalDiff <= 1e-3)
		_ = w.Write([]string{*runID, strconv.Itoa(*seed), *configuration, strconv.Itoa(round), f(maxerr), f(rel), f(dev), f(lossdiff), f(finalDiff), strconv.FormatBool(pass)})
	}
}
