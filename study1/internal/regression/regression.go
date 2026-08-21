package regression

import (
	"encoding/binary"
	"errors"
	"math"
)

const Dimension = 12

const LearningRate = 0.0006653586255336017

type Dataset struct {
	X [][]float64
	Y []float64
}

type Model struct {
	W [Dimension]float64 `json:"w"`
	B float64            `json:"b"`
}

type Gradient struct {
	W [Dimension]float64 `json:"w"`
	B float64            `json:"b"`
	N int                `json:"n"`
}

func (d Dataset) Validate() error {
	if len(d.X) == 0 || len(d.X) != len(d.Y) {
		return errors.New("dataset must have equal nonzero X/Y rows")
	}
	for i := range d.X {
		if len(d.X[i]) != Dimension {
			return errors.New("record dimension is not 12")
		}
		for _, v := range d.X[i] {
			if math.IsNaN(v) || math.IsInf(v, 0) {
				return errors.New("nonfinite feature")
			}
		}
		if math.IsNaN(d.Y[i]) || math.IsInf(d.Y[i], 0) {
			return errors.New("nonfinite target")
		}
	}
	return nil
}

func Residuals(d Dataset, m Model) ([]float64, error) {
	if err := d.Validate(); err != nil {
		return nil, err
	}
	e := make([]float64, len(d.Y))
	for i, x := range d.X {
		v := m.B
		for j := 0; j < Dimension; j++ {
			v += m.W[j] * x[j]
		}
		e[i] = v - d.Y[i]
	}
	return e, nil
}

// FullGradient is for 0.5*mean((prediction-target)^2).
func FullGradient(d Dataset, m Model) (Gradient, error) {
	e, err := Residuals(d, m)
	if err != nil {
		return Gradient{}, err
	}
	g := Gradient{N: len(e)}
	for i := range e {
		g.B += e[i]
		for j := 0; j < Dimension; j++ {
			g.W[j] += e[i] * d.X[i][j]
		}
	}
	inv := 1 / float64(g.N)
	g.B *= inv
	for j := range g.W {
		g.W[j] *= inv
	}
	return g, nil
}

func Aggregate(gs []Gradient) (Gradient, error) {
	var out Gradient
	for _, g := range gs {
		if g.N <= 0 {
			return Gradient{}, errors.New("nonpositive sample count")
		}
		out.N += g.N
		out.B += float64(g.N) * g.B
		for j := range out.W {
			out.W[j] += float64(g.N) * g.W[j]
		}
	}
	if out.N == 0 {
		return Gradient{}, errors.New("no gradients")
	}
	inv := 1 / float64(out.N)
	out.B *= inv
	for j := range out.W {
		out.W[j] *= inv
	}
	return out, nil
}

func Update(m Model, g Gradient, learningRate float64) Model {
	for j := range m.W {
		m.W[j] -= learningRate * g.W[j]
	}
	m.B -= learningRate * g.B
	return m
}

func HalfMSE(d Dataset, m Model) (float64, error) {
	e, err := Residuals(d, m)
	if err != nil {
		return 0, err
	}
	var s float64
	for _, v := range e {
		s += v * v
	}
	return 0.5 * s / float64(len(e)), nil
}

// MarshalDataset is a deterministic binary tensor representation: N, dimension,
// row-major float64 X, then float64 y, all big-endian.
func MarshalDataset(d Dataset) ([]byte, error) {
	if err := d.Validate(); err != nil {
		return nil, err
	}
	b := make([]byte, 8+8*len(d.X)*(Dimension+1))
	binary.BigEndian.PutUint32(b[0:4], uint32(len(d.X)))
	binary.BigEndian.PutUint32(b[4:8], Dimension)
	o := 8
	for i := range d.X {
		for _, v := range d.X[i] {
			binary.BigEndian.PutUint64(b[o:o+8], math.Float64bits(v))
			o += 8
		}
		binary.BigEndian.PutUint64(b[o:o+8], math.Float64bits(d.Y[i]))
		o += 8
	}
	return b, nil
}

func UnmarshalDataset(b []byte) (Dataset, error) {
	if len(b) < 8 {
		return Dataset{}, errors.New("short dataset")
	}
	n, dim := int(binary.BigEndian.Uint32(b[:4])), int(binary.BigEndian.Uint32(b[4:8]))
	if dim != Dimension || n <= 0 || len(b) != 8+8*n*(Dimension+1) {
		return Dataset{}, errors.New("invalid dataset dimensions/length")
	}
	d := Dataset{X: make([][]float64, n), Y: make([]float64, n)}
	o := 8
	for i := 0; i < n; i++ {
		d.X[i] = make([]float64, Dimension)
		for j := 0; j < Dimension; j++ {
			d.X[i][j] = math.Float64frombits(binary.BigEndian.Uint64(b[o : o+8]))
			o += 8
		}
		d.Y[i] = math.Float64frombits(binary.BigEndian.Uint64(b[o : o+8]))
		o += 8
	}
	return d, d.Validate()
}
