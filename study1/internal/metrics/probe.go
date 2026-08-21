package metrics

import (
	"errors"
	"math"
	"math/rand"
)

type ProbeModel struct {
	W          [][]float64
	Mean, SD   []float64
	Classes    []int
	Converged  bool
	Iterations int
}
type Classification struct {
	BalancedAccuracy, MacroF1 float64
	Confusion                 [][]int
	Support                   []int
}

func standardizer(x [][]float64) (mean, sd []float64) {
	d := len(x[0])
	mean = make([]float64, d)
	sd = make([]float64, d)
	for _, r := range x {
		for j, v := range r {
			mean[j] += v
		}
	}
	for j := range mean {
		mean[j] /= float64(len(x))
	}
	for _, r := range x {
		for j, v := range r {
			z := v - mean[j]
			sd[j] += z * z
		}
	}
	for j := range sd {
		sd[j] = math.Sqrt(sd[j] / float64(len(x)))
		if sd[j] == 0 {
			sd[j] = 1
		}
	}
	return
}
func transformed(x []float64, mean, sd []float64) []float64 {
	z := make([]float64, len(x)+1)
	for j := range x {
		z[j] = (x[j] - mean[j]) / sd[j]
	}
	z[len(x)] = 1
	return z
}
func softmax(scores []float64) []float64 {
	m := scores[0]
	for _, x := range scores[1:] {
		if x > m {
			m = x
		}
	}
	var sum float64
	out := make([]float64, len(scores))
	for i, x := range scores {
		out[i] = math.Exp(x - m)
		sum += out[i]
	}
	for i := range out {
		out[i] /= sum
	}
	return out
}
func objective(w [][]float64, x [][]float64, y []int, lambda float64) (float64, [][]float64) {
	k, d := len(w), len(w[0])
	g := make([][]float64, k)
	for c := range g {
		g[c] = make([]float64, d)
	}
	var loss float64
	for i, r := range x {
		s := make([]float64, k)
		for c := 0; c < k; c++ {
			for j, v := range r {
				s[c] += w[c][j] * v
			}
		}
		p := softmax(s)
		loss -= math.Log(math.Max(p[y[i]], 1e-300))
		for c := 0; c < k; c++ {
			delta := p[c]
			if c == y[i] {
				delta--
			}
			for j, v := range r {
				g[c][j] += delta * v
			}
		}
	}
	inv := 1 / float64(len(x))
	loss *= inv
	for c := 0; c < k; c++ {
		for j := 0; j < d-1; j++ {
			loss += .5 * lambda * w[c][j] * w[c][j]
			g[c][j] = g[c][j]*inv + lambda*w[c][j]
		}
		g[c][d-1] *= inv
	}
	return loss, g
}
func FitProbe(x [][]float64, y []int, classes []int, C float64) (ProbeModel, error) {
	if len(x) == 0 || len(x) != len(y) || C <= 0 {
		return ProbeModel{}, errors.New("probe input")
	}
	index := map[int]int{}
	for i, c := range classes {
		index[c] = i
	}
	yi := make([]int, len(y))
	for i, v := range y {
		var ok bool
		yi[i], ok = index[v]
		if !ok {
			return ProbeModel{}, errors.New("unknown class")
		}
	}
	mean, sd := standardizer(x)
	z := make([][]float64, len(x))
	for i := range x {
		z[i] = transformed(x[i], mean, sd)
	}
	w := make([][]float64, len(classes))
	for i := range w {
		w[i] = make([]float64, len(z[0]))
	}
	lambda := 1 / (C * float64(len(x)))
	model := ProbeModel{W: w, Mean: mean, SD: sd, Classes: append([]int(nil), classes...)}
	for iter := 0; iter < 5000; iter++ {
		loss, g := objective(w, z, yi, lambda)
		var norm float64
		for c := range g {
			for _, v := range g[c] {
				norm += v * v
			}
		}
		norm = math.Sqrt(norm)
		model.Iterations = iter + 1
		if norm < 1e-7 {
			model.Converged = true
			break
		}
		step := 1.0
		accepted := false
		for bt := 0; bt < 30; bt++ {
			candidate := make([][]float64, len(w))
			for c := range w {
				candidate[c] = make([]float64, len(w[c]))
				for j := range w[c] {
					candidate[c][j] = w[c][j] - step*g[c][j]
				}
			}
			next, _ := objective(candidate, z, yi, lambda)
			if next <= loss-1e-4*step*norm*norm {
				w = candidate
				accepted = true
				break
			}
			step *= .5
		}
		if !accepted {
			return model, errors.New("probe line search failed")
		}
	}
	model.W = w
	return model, nil
}
func (m ProbeModel) Predict(x [][]float64) []int {
	out := make([]int, len(x))
	for i, r := range x {
		z := transformed(r, m.Mean, m.SD)
		s := make([]float64, len(m.W))
		for c := range m.W {
			for j, v := range z {
				s[c] += m.W[c][j] * v
			}
		}
		p := softmax(s)
		best := 0
		for c := 1; c < len(p); c++ {
			if p[c] > p[best] {
				best = c
			}
		}
		out[i] = m.Classes[best]
	}
	return out
}
func ScoreClassification(truth, pred, classes []int) Classification {
	idx := map[int]int{}
	for i, c := range classes {
		idx[c] = i
	}
	q := Classification{Confusion: make([][]int, len(classes)), Support: make([]int, len(classes))}
	for i := range q.Confusion {
		q.Confusion[i] = make([]int, len(classes))
	}
	for i, y := range truth {
		a, ok := idx[y]
		if !ok {
			continue
		}
		b, ok := idx[pred[i]]
		if !ok {
			continue
		}
		q.Confusion[a][b]++
		q.Support[a]++
	}
	var recall, f1 float64
	for c := range classes {
		tp := q.Confusion[c][c]
		fn := q.Support[c] - tp
		fp := 0
		for r := range classes {
			if r != c {
				fp += q.Confusion[r][c]
			}
		}
		rec := 0.0
		if tp+fn > 0 {
			rec = float64(tp) / float64(tp+fn)
		}
		prec := 0.0
		if tp+fp > 0 {
			prec = float64(tp) / float64(tp+fp)
		}
		recall += rec
		if prec+rec > 0 {
			f1 += 2 * prec * rec / (prec + rec)
		}
	}
	q.BalancedAccuracy = recall / float64(len(classes))
	q.MacroF1 = f1 / float64(len(classes))
	return q
}
func ShuffleLabels(y []int, seed int64) []int {
	out := append([]int(nil), y...)
	rand.New(rand.NewSource(seed)).Shuffle(len(out), func(i, j int) { out[i], out[j] = out[j], out[i] })
	return out
}
