package ckks

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"os"

	"delegatedfl/study1/internal/regression"

	"github.com/tuneinsight/lattigo/v5/core/rlwe"
	"github.com/tuneinsight/lattigo/v5/he/hefloat"
)

var Literal = hefloat.ParametersLiteral{LogN: 14, LogQ: []int{55, 45, 45, 45, 45}, LogP: []int{61}, LogDefaultScale: 45}

// PackedSampleCapacity is the frozen power-of-two reduction width. The frozen
// Study-I owner shard contains 120 learning records; the remaining eight slots
// are encoded as zero and are included only to make the rotation tree exact.
const PackedSampleCapacity = 128

var RequiredRotations = []int{1, 2, 4, 8, 16, 32, 64}

type Authority struct {
	Params hefloat.Parameters
	SK     *rlwe.SecretKey
	PK     *rlwe.PublicKey
	RLK    *rlwe.RelinearizationKey
	GKS    []*rlwe.GaloisKey
}
type Encryptor struct {
	Params    hefloat.Parameters
	Encoder   *hefloat.Encoder
	Encryptor *rlwe.Encryptor
}
type Evaluator struct {
	Params    hefloat.Parameters
	Eval      *hefloat.Evaluator
	Encoder   *hefloat.Encoder
	Encryptor *rlwe.Encryptor
}
type Decryptor struct {
	Params    hefloat.Parameters
	Encoder   *hefloat.Encoder
	Decryptor *rlwe.Decryptor
}

type EncryptedDataset struct {
	X [regression.Dimension]*rlwe.Ciphertext
	Y *rlwe.Ciphertext
	N int
}
type EncryptedGradient struct {
	W [regression.Dimension]*rlwe.Ciphertext
	B *rlwe.Ciphertext
	N int
}

func NewAuthority() (*Authority, error) {
	p, err := hefloat.NewParametersFromLiteral(Literal)
	if err != nil {
		return nil, err
	}
	kg := rlwe.NewKeyGenerator(p)
	sk, pk := kg.GenKeyPairNew()
	rlk := kg.GenRelinearizationKeyNew(sk)
	galEls := make([]uint64, len(RequiredRotations))
	for i, rotation := range RequiredRotations {
		galEls[i] = p.GaloisElementForRotation(rotation)
	}
	gks := kg.GenGaloisKeysNew(galEls, sk)
	return &Authority{p, sk, pk, rlk, gks}, nil
}
func NewEncryptor(p hefloat.Parameters, pk *rlwe.PublicKey) *Encryptor {
	return &Encryptor{p, hefloat.NewEncoder(p), rlwe.NewEncryptor(p, pk)}
}
func NewEvaluator(p hefloat.Parameters, rlk *rlwe.RelinearizationKey, gks []*rlwe.GaloisKey, pk *rlwe.PublicKey) *Evaluator {
	return &Evaluator{p, hefloat.NewEvaluator(p, rlwe.NewMemEvaluationKeySet(rlk, gks...)), hefloat.NewEncoder(p), rlwe.NewEncryptor(p, pk)}
}
func NewDecryptor(p hefloat.Parameters, sk *rlwe.SecretKey) *Decryptor {
	return &Decryptor{p, hefloat.NewEncoder(p), rlwe.NewDecryptor(p, sk)}
}

func (e *Encryptor) Scalar(v float64) (*rlwe.Ciphertext, error) {
	pt := hefloat.NewPlaintext(e.Params, e.Params.MaxLevel())
	if err := e.Encoder.Encode([]float64{v}, pt); err != nil {
		return nil, err
	}
	return e.Encryptor.EncryptNew(pt)
}
func (e *Encryptor) Vector(v []float64) (*rlwe.Ciphertext, error) {
	if len(v) == 0 || len(v) > PackedSampleCapacity {
		return nil, fmt.Errorf("packed vector length %d outside 1..%d", len(v), PackedSampleCapacity)
	}
	values := make([]float64, PackedSampleCapacity)
	copy(values, v)
	pt := hefloat.NewPlaintext(e.Params, e.Params.MaxLevel())
	if err := e.Encoder.Encode(values, pt); err != nil {
		return nil, err
	}
	return e.Encryptor.EncryptNew(pt)
}
func (d *Decryptor) Scalar(ct *rlwe.Ciphertext) (float64, error) {
	pt := d.Decryptor.DecryptNew(ct)
	values := make([]float64, 1)
	if err := d.Encoder.Decode(pt, values); err != nil {
		return 0, err
	}
	return values[0], nil
}
func (d *Decryptor) Vector(ct *rlwe.Ciphertext, n int) ([]float64, error) {
	if n < 1 || n > PackedSampleCapacity {
		return nil, fmt.Errorf("packed vector length %d outside 1..%d", n, PackedSampleCapacity)
	}
	pt := d.Decryptor.DecryptNew(ct)
	values := make([]float64, PackedSampleCapacity)
	if err := d.Encoder.Decode(pt, values); err != nil {
		return nil, err
	}
	return append([]float64(nil), values[:n]...), nil
}
func (e *Encryptor) Dataset(d regression.Dataset) (EncryptedDataset, error) {
	if err := d.Validate(); err != nil {
		return EncryptedDataset{}, err
	}
	if len(d.X) > PackedSampleCapacity {
		return EncryptedDataset{}, fmt.Errorf("dataset has %d records; packed capacity is %d", len(d.X), PackedSampleCapacity)
	}
	out := EncryptedDataset{N: len(d.X)}
	for j := 0; j < regression.Dimension; j++ {
		column := make([]float64, len(d.X))
		for i := range d.X {
			column[i] = d.X[i][j]
		}
		ct, err := e.Vector(column)
		if err != nil {
			return out, err
		}
		out.X[j] = ct
	}
	ct, err := e.Vector(d.Y)
	if err != nil {
		return out, err
	}
	out.Y = ct
	return out, nil
}

func (e *Evaluator) PredictionAndResiduals(d EncryptedDataset, m regression.Model) (*rlwe.Ciphertext, error) {
	if d.N < 1 || d.N > PackedSampleCapacity || d.Y == nil {
		return nil, errors.New("invalid encrypted dataset")
	}
	var pred *rlwe.Ciphertext
	for j := 0; j < regression.Dimension; j++ {
		if d.X[j] == nil {
			return nil, fmt.Errorf("nil packed feature %d", j)
		}
		term, err := e.Eval.MulNew(d.X[j], m.W[j])
		if err != nil {
			return nil, err
		}
		if err = e.Eval.Rescale(term, term); err != nil {
			return nil, err
		}
		if pred == nil {
			pred = term
		} else if pred, err = e.Eval.AddNew(pred, term); err != nil {
			return nil, err
		}
	}
	var err error
	bias := make([]float64, PackedSampleCapacity)
	for i := 0; i < d.N; i++ {
		bias[i] = m.B
	}
	if pred, err = e.Eval.AddNew(pred, bias); err != nil {
		return nil, err
	}
	y := d.Y.CopyNew()
	if y.Level() > pred.Level() {
		e.Eval.DropLevel(y, y.Level()-pred.Level())
	}
	return e.Eval.SubNew(pred, y)
}

func (e *Evaluator) sumPacked(ct *rlwe.Ciphertext) (*rlwe.Ciphertext, error) {
	out := ct.CopyNew()
	for _, rotation := range RequiredRotations {
		rotated, err := e.Eval.RotateNew(out, rotation)
		if err != nil {
			return nil, err
		}
		if out, err = e.Eval.AddNew(out, rotated); err != nil {
			return nil, err
		}
	}
	return out, nil
}

func (e *Evaluator) GradientOpenedResidual(d EncryptedDataset, residual []float64) (EncryptedGradient, error) {
	if len(residual) != d.N || d.N == 0 {
		return EncryptedGradient{}, errors.New("residual count")
	}
	var g EncryptedGradient
	g.N = d.N
	for j := 0; j < regression.Dimension; j++ {
		scaledResidual := make([]float64, PackedSampleCapacity)
		for i := range residual {
			scaledResidual[i] = residual[i] / float64(g.N)
		}
		term, err := e.Eval.MulNew(d.X[j], scaledResidual)
		if err != nil {
			return g, err
		}
		if err = e.Eval.Rescale(term, term); err != nil {
			return g, err
		}
		if g.W[j], err = e.sumPacked(term); err != nil {
			return g, err
		}
	}
	b, err := eScalarFromResidual(e, residual)
	if err != nil {
		return g, err
	}
	g.B = b
	return g, nil
}
func eScalarFromResidual(e *Evaluator, r []float64) (*rlwe.Ciphertext, error) {
	var v float64
	for _, x := range r {
		v += x
	}
	v /= float64(len(r))
	pt := hefloat.NewPlaintext(e.Params, e.Params.MaxLevel())
	if err := e.Encoder.Encode([]float64{v}, pt); err != nil {
		return nil, err
	}
	return e.Encryptor.EncryptNew(pt)
}

func (e *Evaluator) GradientEncryptedResidual(d EncryptedDataset, residual *rlwe.Ciphertext) (EncryptedGradient, error) {
	if residual == nil || d.N == 0 {
		return EncryptedGradient{}, errors.New("residual count")
	}
	var g EncryptedGradient
	g.N = d.N
	scaledResidual, err := e.Eval.MulNew(residual, 1/float64(g.N))
	if err != nil {
		return g, err
	}
	if err = e.Eval.Rescale(scaledResidual, scaledResidual); err != nil {
		return g, err
	}
	if g.B, err = e.sumPacked(scaledResidual); err != nil {
		return g, err
	}
	for j := 0; j < regression.Dimension; j++ {
		x := d.X[j].CopyNew()
		if x.Level() > residual.Level() {
			e.Eval.DropLevel(x, x.Level()-residual.Level())
		}
		term, err := e.Eval.MulRelinNew(residual, x)
		if err != nil {
			return g, err
		}
		if err = e.Eval.Rescale(term, term); err != nil {
			return g, err
		}
		scaled, err := e.Eval.MulNew(term, 1/float64(g.N))
		if err != nil {
			return g, err
		}
		if err = e.Eval.Rescale(scaled, scaled); err != nil {
			return g, err
		}
		if g.W[j], err = e.sumPacked(scaled); err != nil {
			return g, err
		}
	}
	return g, nil
}

func (d *Decryptor) Gradient(g EncryptedGradient) (regression.Gradient, error) {
	out := regression.Gradient{N: g.N}
	var err error
	for j := range out.W {
		if g.W[j] == nil {
			return out, fmt.Errorf("nil W[%d]", j)
		}
		if out.W[j], err = d.Scalar(g.W[j]); err != nil {
			return out, err
		}
	}
	if g.B == nil {
		return out, errors.New("nil bias")
	}
	out.B, err = d.Scalar(g.B)
	return out, err
}

// Binary ciphertext containers keep exact ciphertext serialization visible to TCP accounting.
func MarshalCiphertexts(cts []*rlwe.Ciphertext) ([]byte, error) {
	size := 4
	parts := make([][]byte, len(cts))
	for i, ct := range cts {
		b, err := ct.MarshalBinary()
		if err != nil {
			return nil, err
		}
		parts[i] = b
		size += 4 + len(b)
	}
	out := make([]byte, size)
	binary.BigEndian.PutUint32(out[:4], uint32(len(cts)))
	o := 4
	for _, b := range parts {
		binary.BigEndian.PutUint32(out[o:o+4], uint32(len(b)))
		o += 4
		copy(out[o:], b)
		o += len(b)
	}
	return out, nil
}
func UnmarshalCiphertexts(b []byte) ([]*rlwe.Ciphertext, error) {
	if len(b) < 4 {
		return nil, errors.New("short ciphertext list")
	}
	n := int(binary.BigEndian.Uint32(b[:4]))
	out := make([]*rlwe.Ciphertext, n)
	o := 4
	for i := 0; i < n; i++ {
		if o+4 > len(b) {
			return nil, errors.New("short ciphertext length")
		}
		l := int(binary.BigEndian.Uint32(b[o : o+4]))
		o += 4
		if l < 1 || o+l > len(b) {
			return nil, errors.New("bad ciphertext length")
		}
		ct := new(rlwe.Ciphertext)
		if err := ct.UnmarshalBinary(b[o : o+l]); err != nil {
			return nil, err
		}
		out[i] = ct
		o += l
	}
	if o != len(b) {
		return nil, errors.New("trailing ciphertext bytes")
	}
	return out, nil
}

func MarshalPackedCiphertext(ct *rlwe.Ciphertext, n int) ([]byte, error) {
	if ct == nil || n < 1 || n > PackedSampleCapacity {
		return nil, errors.New("invalid packed ciphertext")
	}
	body, err := ct.MarshalBinary()
	if err != nil {
		return nil, err
	}
	out := make([]byte, 8+len(body))
	binary.BigEndian.PutUint32(out[:4], uint32(n))
	binary.BigEndian.PutUint32(out[4:8], uint32(len(body)))
	copy(out[8:], body)
	return out, nil
}

func UnmarshalPackedCiphertext(b []byte) (*rlwe.Ciphertext, int, error) {
	if len(b) < 8 {
		return nil, 0, errors.New("short packed ciphertext")
	}
	n := int(binary.BigEndian.Uint32(b[:4]))
	l := int(binary.BigEndian.Uint32(b[4:8]))
	if n < 1 || n > PackedSampleCapacity || l < 1 || len(b) != 8+l {
		return nil, 0, errors.New("invalid packed ciphertext dimensions")
	}
	ct := new(rlwe.Ciphertext)
	if err := ct.UnmarshalBinary(b[8:]); err != nil {
		return nil, 0, err
	}
	return ct, n, nil
}

func writeExclusive(path string, b []byte) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	if _, err = f.Write(b); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}
func (a *Authority) Save(dir string) error {
	if err := os.Mkdir(dir, 0o700); err != nil {
		return err
	}
	params, _ := a.Params.MarshalBinary()
	sk, _ := a.SK.MarshalBinary()
	pk, _ := a.PK.MarshalBinary()
	rlk, _ := a.RLK.MarshalBinary()
	gks := make([][]byte, len(a.GKS))
	for i := range a.GKS {
		gks[i], _ = a.GKS[i].MarshalBinary()
	}
	rotationKeys := marshalParts(gks...)
	for name, b := range map[string][]byte{"parameters.bin": params, "secret.key": sk, "public.key": pk, "relinearization.key": rlk, "rotation.keys": rotationKeys} {
		if err := writeExclusive(dir+"/"+name, b); err != nil {
			return err
		}
	}
	return nil
}
func LoadAuthority(dir string) (*Authority, error) {
	pb, e := os.ReadFile(dir + "/parameters.bin")
	if e != nil {
		return nil, e
	}
	var p hefloat.Parameters
	if e = p.UnmarshalBinary(pb); e != nil {
		return nil, e
	}
	skb, e := os.ReadFile(dir + "/secret.key")
	if e != nil {
		return nil, e
	}
	sk := rlwe.NewSecretKey(p)
	if e = sk.UnmarshalBinary(skb); e != nil {
		return nil, e
	}
	pkb, e := os.ReadFile(dir + "/public.key")
	if e != nil {
		return nil, e
	}
	pk := rlwe.NewPublicKey(p)
	if e = pk.UnmarshalBinary(pkb); e != nil {
		return nil, e
	}
	rlkb, e := os.ReadFile(dir + "/relinearization.key")
	if e != nil {
		return nil, e
	}
	rlk := rlwe.NewRelinearizationKey(p)
	if e = rlk.UnmarshalBinary(rlkb); e != nil {
		return nil, e
	}
	gkb, e := os.ReadFile(dir + "/rotation.keys")
	if e != nil {
		return nil, e
	}
	gkParts, e := unmarshalParts(gkb, len(RequiredRotations))
	if e != nil {
		return nil, e
	}
	gks := make([]*rlwe.GaloisKey, len(gkParts))
	for i := range gkParts {
		gks[i] = rlwe.NewGaloisKey(p)
		if e = gks[i].UnmarshalBinary(gkParts[i]); e != nil {
			return nil, e
		}
	}
	return &Authority{p, sk, pk, rlk, gks}, nil
}

func marshalParts(parts ...[]byte) []byte {
	var b bytes.Buffer
	for _, p := range parts {
		_ = binary.Write(&b, binary.BigEndian, uint32(len(p)))
		b.Write(p)
	}
	return b.Bytes()
}
func unmarshalParts(b []byte, n int) ([][]byte, error) {
	out := make([][]byte, n)
	o := 0
	for i := 0; i < n; i++ {
		if o+4 > len(b) {
			return nil, errors.New("short key bundle")
		}
		l := int(binary.BigEndian.Uint32(b[o : o+4]))
		o += 4
		if l < 1 || o+l > len(b) {
			return nil, errors.New("invalid key part")
		}
		out[i] = append([]byte(nil), b[o:o+l]...)
		o += l
	}
	if o != len(b) {
		return nil, errors.New("trailing key bytes")
	}
	return out, nil
}
func (a *Authority) PublicBundle() ([]byte, error) {
	p, e := a.Params.MarshalBinary()
	if e != nil {
		return nil, e
	}
	pk, e := a.PK.MarshalBinary()
	if e != nil {
		return nil, e
	}
	return marshalParts(p, pk), nil
}
func (a *Authority) EvaluationBundle() ([]byte, error) {
	p, e := a.Params.MarshalBinary()
	if e != nil {
		return nil, e
	}
	pk, e := a.PK.MarshalBinary()
	if e != nil {
		return nil, e
	}
	rlk, e := a.RLK.MarshalBinary()
	if e != nil {
		return nil, e
	}
	gks := make([][]byte, len(a.GKS))
	for i := range a.GKS {
		if gks[i], e = a.GKS[i].MarshalBinary(); e != nil {
			return nil, e
		}
	}
	return marshalParts(p, pk, rlk, marshalParts(gks...)), nil
}
func ParsePublicBundle(b []byte) (hefloat.Parameters, *rlwe.PublicKey, error) {
	parts, e := unmarshalParts(b, 2)
	if e != nil {
		return hefloat.Parameters{}, nil, e
	}
	var p hefloat.Parameters
	if e = p.UnmarshalBinary(parts[0]); e != nil {
		return p, nil, e
	}
	pk := rlwe.NewPublicKey(p)
	if e = pk.UnmarshalBinary(parts[1]); e != nil {
		return p, nil, e
	}
	return p, pk, nil
}
func ParseEvaluationBundle(b []byte) (hefloat.Parameters, *rlwe.PublicKey, *rlwe.RelinearizationKey, []*rlwe.GaloisKey, error) {
	parts, e := unmarshalParts(b, 4)
	if e != nil {
		return hefloat.Parameters{}, nil, nil, nil, e
	}
	var p hefloat.Parameters
	if e = p.UnmarshalBinary(parts[0]); e != nil {
		return p, nil, nil, nil, e
	}
	pk := rlwe.NewPublicKey(p)
	if e = pk.UnmarshalBinary(parts[1]); e != nil {
		return p, nil, nil, nil, e
	}
	rlk := rlwe.NewRelinearizationKey(p)
	if e = rlk.UnmarshalBinary(parts[2]); e != nil {
		return p, nil, nil, nil, e
	}
	gkParts, e := unmarshalParts(parts[3], len(RequiredRotations))
	if e != nil {
		return p, nil, nil, nil, e
	}
	gks := make([]*rlwe.GaloisKey, len(gkParts))
	for i := range gkParts {
		gks[i] = rlwe.NewGaloisKey(p)
		if e = gks[i].UnmarshalBinary(gkParts[i]); e != nil {
			return p, nil, nil, nil, e
		}
	}
	return p, pk, rlk, gks, nil
}
func MarshalDataset(d EncryptedDataset) ([]byte, error) {
	if d.N < 1 || d.N > PackedSampleCapacity || d.Y == nil {
		return nil, errors.New("invalid encrypted dataset")
	}
	flat := make([]*rlwe.Ciphertext, 0, regression.Dimension+1)
	for j := range d.X {
		if d.X[j] == nil {
			return nil, fmt.Errorf("nil packed feature %d", j)
		}
		flat = append(flat, d.X[j])
	}
	flat = append(flat, d.Y)
	cts, e := MarshalCiphertexts(flat)
	if e != nil {
		return nil, e
	}
	head := make([]byte, 8)
	binary.BigEndian.PutUint32(head[:4], uint32(d.N))
	binary.BigEndian.PutUint32(head[4:], regression.Dimension)
	return append(head, cts...), nil
}
func UnmarshalDataset(b []byte) (EncryptedDataset, error) {
	if len(b) < 8 {
		return EncryptedDataset{}, errors.New("short encrypted dataset")
	}
	n, dim := int(binary.BigEndian.Uint32(b[:4])), int(binary.BigEndian.Uint32(b[4:8]))
	if n < 1 || n > PackedSampleCapacity || dim != regression.Dimension {
		return EncryptedDataset{}, errors.New("encrypted dataset dimensions")
	}
	flat, e := UnmarshalCiphertexts(b[8:])
	if e != nil {
		return EncryptedDataset{}, e
	}
	if len(flat) != regression.Dimension+1 {
		return EncryptedDataset{}, errors.New("encrypted ciphertext count")
	}
	out := EncryptedDataset{N: n}
	for j := 0; j < regression.Dimension; j++ {
		out.X[j] = flat[j]
	}
	out.Y = flat[regression.Dimension]
	return out, nil
}
func MarshalGradient(g EncryptedGradient) ([]byte, error) {
	flat := make([]*rlwe.Ciphertext, 0, regression.Dimension+1)
	flat = append(flat, g.W[:]...)
	flat = append(flat, g.B)
	body, e := MarshalCiphertexts(flat)
	if e != nil {
		return nil, e
	}
	head := make([]byte, 4)
	binary.BigEndian.PutUint32(head, uint32(g.N))
	return append(head, body...), nil
}
func UnmarshalGradient(b []byte) (EncryptedGradient, error) {
	if len(b) < 4 {
		return EncryptedGradient{}, errors.New("short gradient")
	}
	g := EncryptedGradient{N: int(binary.BigEndian.Uint32(b[:4]))}
	flat, e := UnmarshalCiphertexts(b[4:])
	if e != nil {
		return g, e
	}
	if g.N < 1 || len(flat) != regression.Dimension+1 {
		return g, errors.New("gradient dimensions")
	}
	copy(g.W[:], flat[:regression.Dimension])
	g.B = flat[regression.Dimension]
	return g, nil
}
