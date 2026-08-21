package protocol

import (
	"bytes"
	"testing"
)

func TestFrameAccountsActualBytes(t *testing.T) {
	p := []byte{0, 1, 2, 3, 4}
	h := NewHeader("O_1", "E", "task", "O_1", 3, "TENSOR", p)
	var b bytes.Buffer
	sent, err := Write(&b, Message{h, p}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if sent != uint64(b.Len()) {
		t.Fatalf("sent=%d len=%d", sent, b.Len())
	}
	m, got, err := Read(&b, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got != sent || !bytes.Equal(m.Payload, p) {
		t.Fatalf("got=%d sent=%d", got, sent)
	}
}
