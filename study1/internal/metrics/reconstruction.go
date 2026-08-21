package metrics

import (
	"errors"
	"math"
	"sort"

	"delegatedfl/study1/internal/regression"
)

const Unknowns = regression.Dimension + 1

type ReconstructionDiagnostics struct {
	Equations       int       `json:"equations"`
	Unknowns        int       `json:"unknowns"`
	Rank            int       `json:"rank"`
	SingularValues  []float64 `json:"singular_values"`
	ConditionNumber float64   `json:"condition_number"`
	Solver          string    `json:"solver"`
	Regularization  float64   `json:"regularization"`
	RankTolerance   float64   `json:"rank_tolerance"`
	Converged       bool      `json:"converged"`
}

type Reconstruction struct {
	Values      [Unknowns]float64         `json:"values"`
	Diagnostics ReconstructionDiagnostics `json:"diagnostics"`
}

// oneSidedJacobi computes a direct thin SVD A=U*S*V^T without normal equations.
func oneSidedJacobi(a [][]float64) (u [][]float64, s []float64, v [][]float64, converged bool, err error) {
	m := len(a)
	if m == 0 {
		return nil, nil, nil, false, errors.New("no equations")
	}
	n := len(a[0])
	if n == 0 {
		return nil, nil, nil, false, errors.New("no unknowns")
	}
	b := make([][]float64, m)
	for i := range a {
		if len(a[i]) != n {
			return nil, nil, nil, false, errors.New("ragged matrix")
		}
		b[i] = append([]float64(nil), a[i]...)
	}
	v = make([][]float64, n)
	for i := range v {
		v[i] = make([]float64, n)
		v[i][i] = 1
	}
	const eps = 2.220446049250313e-16
	for sweep := 0; sweep < 200; sweep++ {
		changed := false
		for p := 0; p < n-1; p++ {
			for q := p + 1; q < n; q++ {
				var alpha, beta, gamma float64
				for i := 0; i < m; i++ {
					alpha += b[i][p] * b[i][p]
					beta += b[i][q] * b[i][q]
					gamma += b[i][p] * b[i][q]
				}
				if alpha == 0 || beta == 0 || math.Abs(gamma) <= eps*math.Sqrt(alpha*beta)*float64(m) {
					continue
				}
				zeta := (beta - alpha) / (2 * gamma)
				t := math.Copysign(1, zeta) / (math.Abs(zeta) + math.Sqrt(1+zeta*zeta))
				c := 1 / math.Sqrt(1+t*t)
				sn := c * t
				for i := 0; i < m; i++ {
					bp, bq := b[i][p], b[i][q]
					b[i][p] = c*bp - sn*bq
					b[i][q] = sn*bp + c*bq
				}
				for i := 0; i < n; i++ {
					vp, vq := v[i][p], v[i][q]
					v[i][p] = c*vp - sn*vq
					v[i][q] = sn*vp + c*vq
				}
				changed = true
			}
		}
		if !changed {
			converged = true
			break
		}
	}
	s = make([]float64, n)
	u = make([][]float64, m)
	for i := range u {
		u[i] = make([]float64, n)
	}
	for j := 0; j < n; j++ {
		for i := 0; i < m; i++ {
			s[j] += b[i][j] * b[i][j]
		}
		s[j] = math.Sqrt(s[j])
		if s[j] > 0 {
			for i := 0; i < m; i++ {
				u[i][j] = b[i][j] / s[j]
			}
		}
	}
	order := make([]int, n)
	for i := range order {
		order[i] = i
	}
	sort.Slice(order, func(i, j int) bool { return s[order[i]] > s[order[j]] })
	ss := append([]float64(nil), s...)
	uu := make([][]float64, m)
	vv := make([][]float64, n)
	for i := range uu {
		uu[i] = make([]float64, n)
	}
	for i := range vv {
		vv[i] = make([]float64, n)
	}
	for newj, oldj := range order {
		s[newj] = ss[oldj]
		for i := 0; i < m; i++ {
			uu[i][newj] = u[i][oldj]
		}
		for i := 0; i < n; i++ {
			vv[i][newj] = v[i][oldj]
		}
	}
	return uu, s, vv, converged, nil
}

func Reconstruct(models []regression.Model, residuals []float64) (Reconstruction, error) {
	if len(models) != len(residuals) || len(models) == 0 {
		return Reconstruction{}, errors.New("models/residual count")
	}
	a := make([][]float64, len(models))
	rhs := make([]float64, len(models))
	for i, m := range models {
		a[i] = make([]float64, Unknowns)
		copy(a[i], m.W[:])
		a[i][Unknowns-1] = -1
		rhs[i] = residuals[i] - m.B
	}
	u, s, v, converged, err := oneSidedJacobi(a)
	if err != nil {
		return Reconstruction{}, err
	}
	tol := float64(max(len(a), Unknowns)) * s[0] * 2.220446049250313e-16
	rank := 0
	for _, x := range s {
		if x > tol {
			rank++
		}
	}
	cond := math.Inf(1)
	if rank > 0 {
		cond = s[0] / s[rank-1]
	}
	var x [Unknowns]float64
	for j := 0; j < rank; j++ {
		var ub float64
		for i := range rhs {
			ub += u[i][j] * rhs[i]
		}
		coefficient := ub / s[j]
		for k := 0; k < Unknowns; k++ {
			x[k] += v[k][j] * coefficient
		}
	}
	return Reconstruction{x, ReconstructionDiagnostics{len(a), Unknowns, rank, s, cond, "direct_one_sided_Jacobi_SVD", 0, tol, converged}}, nil
}

type ContinuousMetrics struct{ MAE, RMSE, R2 float64 }

func Continuous(truth, pred []float64) ContinuousMetrics {
	var mae, mse, mean float64
	for _, x := range truth {
		mean += x
	}
	mean /= float64(len(truth))
	var total float64
	for i, x := range truth {
		d := pred[i] - x
		mae += math.Abs(d)
		mse += d * d
		t := x - mean
		total += t * t
	}
	r2 := math.NaN()
	if total > 0 {
		r2 = 1 - mse/total
	}
	return ContinuousMetrics{mae / float64(len(truth)), math.Sqrt(mse / float64(len(truth))), r2}
}

type FeatureMetrics struct{ StandardizedRMSE, RelativeL2, Cosine float64 }

func Features(truth, pred [][]float64, mean, sd []float64) FeatureMetrics {
	var standardized, diff, normT, dot, normP float64
	count := 0
	for i := range truth {
		for j := range truth[i] {
			d := pred[i][j] - truth[i][j]
			if sd[j] > 0 {
				standardized += (d / sd[j]) * (d / sd[j])
				count++
			}
			diff += d * d
			normT += truth[i][j] * truth[i][j]
			dot += truth[i][j] * pred[i][j]
			normP += pred[i][j] * pred[i][j]
		}
	}
	return FeatureMetrics{math.Sqrt(standardized / float64(count)), math.Sqrt(diff) / math.Sqrt(normT), dot / (math.Sqrt(normT) * math.Sqrt(normP))}
}
