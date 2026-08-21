package regression

import (
	"math"
	"testing"
)

func TestGradientAndRoundTrip(t *testing.T) {
	d := Dataset{X: [][]float64{{1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}, {3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}}, Y: []float64{2, 6}}
	g, err := FullGradient(d, Model{})
	if err != nil {
		t.Fatal(err)
	}
	if math.Abs(g.W[0]+10) > 1e-12 || math.Abs(g.B+4) > 1e-12 {
		t.Fatalf("bad gradient: %+v", g)
	}
	b, _ := MarshalDataset(d)
	d2, err := UnmarshalDataset(b)
	if err != nil || d2.Y[1] != 6 {
		t.Fatalf("round trip: %v %+v", err, d2)
	}
}
