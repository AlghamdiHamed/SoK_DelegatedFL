package metrics

import (
	"delegatedfl/study1/internal/regression"
	"math"
	"testing"
)

func TestReconstructFullRank(t *testing.T) {
	var truth [Unknowns]float64
	for i := range truth {
		truth[i] = float64(i + 1)
	}
	models := make([]regression.Model, 20)
	res := make([]float64, 20)
	for r := range models {
		for j := 0; j < regression.Dimension; j++ {
			models[r].W[j] = math.Sin(float64((r + 1) * (j + 1)))
		}
		models[r].B = .1 * float64(r)
		var e float64
		for j := 0; j < regression.Dimension; j++ {
			e += models[r].W[j] * truth[j]
		}
		e += models[r].B - truth[12]
		res[r] = e
	}
	x, e := Reconstruct(models, res)
	if e != nil {
		t.Fatal(e)
	}
	if x.Diagnostics.Rank != 13 {
		t.Fatalf("rank=%d singular=%v", x.Diagnostics.Rank, x.Diagnostics.SingularValues)
	}
	for i := range truth {
		if math.Abs(x.Values[i]-truth[i]) > 1e-8 {
			t.Fatalf("i=%d got=%g want=%g", i, x.Values[i], truth[i])
		}
	}
}
func TestNoUniqueClaimAtLowR(t *testing.T) {
	models := make([]regression.Model, 5)
	r := make([]float64, 5)
	x, e := Reconstruct(models, r)
	if e != nil {
		t.Fatal(e)
	}
	if x.Diagnostics.Rank >= Unknowns {
		t.Fatal("underdetermined system reported full rank")
	}
}
