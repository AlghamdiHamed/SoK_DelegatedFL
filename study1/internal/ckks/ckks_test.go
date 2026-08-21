package ckks

import (
	"math"
	"testing"

	"delegatedfl/study1/internal/regression"
)

func TestOpenedAndEncryptedResidualGradients(t *testing.T) {
	d := regression.Dataset{X: [][]float64{{1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}, {3, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}}, Y: []float64{4, 2}}
	m := regression.Model{B: .25}
	m.W[0] = .5
	m.W[1] = -.25
	want, err := regression.FullGradient(d, m)
	if err != nil {
		t.Fatal(err)
	}
	a, err := NewAuthority()
	if err != nil {
		t.Fatal(err)
	}
	ed, err := NewEncryptor(a.Params, a.PK).Dataset(d)
	if err != nil {
		t.Fatal(err)
	}
	eval := NewEvaluator(a.Params, a.RLK, a.GKS, a.PK)
	residual, err := eval.PredictionAndResiduals(ed, m)
	if err != nil {
		t.Fatal(err)
	}
	dec := NewDecryptor(a.Params, a.SK)
	opened, err := dec.Vector(residual, ed.N)
	if err != nil {
		t.Fatal(err)
	}
	gOR, err := eval.GradientOpenedResidual(ed, opened)
	if err != nil {
		t.Fatal(err)
	}
	gotOR, err := dec.Gradient(gOR)
	if err != nil {
		t.Fatal(err)
	}
	gER, err := eval.GradientEncryptedResidual(ed, residual)
	if err != nil {
		t.Fatal(err)
	}
	gotER, err := dec.Gradient(gER)
	if err != nil {
		t.Fatal(err)
	}
	for name, got := range map[string]regression.Gradient{"I-OR": gotOR, "I-ER": gotER} {
		for j := range want.W {
			if math.Abs(got.W[j]-want.W[j]) > 1e-4 {
				t.Fatalf("%s W[%d]=%.8g want %.8g", name, j, got.W[j], want.W[j])
			}
		}
		if math.Abs(got.B-want.B) > 1e-4 {
			t.Fatalf("%s B=%.8g want %.8g", name, got.B, want.B)
		}
	}
}
