package protocol

import (
	"bufio"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

const Version = "dfl-exp/1"
const MaxFrameBytes uint64 = 1 << 30

type Header struct {
	ProtocolVersion string `json:"protocol_version"`
	Sender          string `json:"sender"`
	Receiver        string `json:"receiver"`
	TaskID          string `json:"task_id"`
	ClientID        string `json:"client_id"`
	RoundID         int    `json:"round_id"`
	MessageType     string `json:"message_type"`
	PayloadBytes    uint64 `json:"payload_bytes"`
	PayloadSHA256   string `json:"payload_sha256"`
	TimestampUTC    string `json:"timestamp_utc"`
}

type Message struct {
	Header  Header
	Payload []byte
}

type LogRecord struct {
	Header
	Direction       string `json:"direction"`
	SerializedBytes uint64 `json:"serialized_bytes"`
	SocketBytes     uint64 `json:"socket_bytes"`
}

type JSONLLogger struct {
	mu sync.Mutex
	f  *os.File
}

func NewJSONLLogger(path string) (*JSONLLogger, error) {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return nil, err
	}
	return &JSONLLogger{f: f}, nil
}
func (l *JSONLLogger) Close() error { return l.f.Close() }
func (l *JSONLLogger) Log(r LogRecord) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	b, err := json.Marshal(r)
	if err != nil {
		return err
	}
	if _, err = l.f.Write(append(b, '\n')); err != nil {
		return err
	}
	return l.f.Sync()
}

func NewHeader(sender, receiver, task, client string, round int, kind string, payload []byte) Header {
	h := sha256.Sum256(payload)
	return Header{Version, sender, receiver, task, client, round, kind, uint64(len(payload)), hex.EncodeToString(h[:]), time.Now().UTC().Format(time.RFC3339Nano)}
}

func canonicalHeader(h Header) ([]byte, error) { return json.Marshal(h) }

func Write(w io.Writer, m Message, logger *JSONLLogger) (uint64, error) {
	if m.Header.ProtocolVersion != Version {
		return 0, errors.New("protocol version mismatch")
	}
	if uint64(len(m.Payload)) != m.Header.PayloadBytes {
		return 0, errors.New("payload length mismatch")
	}
	hash := sha256.Sum256(m.Payload)
	if hex.EncodeToString(hash[:]) != m.Header.PayloadSHA256 {
		return 0, errors.New("payload hash mismatch")
	}
	hb, err := canonicalHeader(m.Header)
	if err != nil {
		return 0, err
	}
	bodyLen := uint64(4 + len(hb) + len(m.Payload))
	if bodyLen > MaxFrameBytes {
		return 0, errors.New("frame too large")
	}
	prefix := make([]byte, 12)
	binary.BigEndian.PutUint64(prefix[:8], bodyLen)
	binary.BigEndian.PutUint32(prefix[8:], uint32(len(hb)))
	parts := netBuffers{prefix, hb, m.Payload}
	n, err := parts.writeFull(w)
	serialized := uint64(8) + bodyLen
	if err == nil && uint64(n) != serialized {
		err = io.ErrShortWrite
	}
	if logger != nil {
		_ = logger.Log(LogRecord{m.Header, "sent", serialized, uint64(n)})
	}
	return uint64(n), err
}

type netBuffers [][]byte

func (bs netBuffers) writeFull(w io.Writer) (int, error) {
	total := 0
	for _, b := range bs {
		for len(b) > 0 {
			n, err := w.Write(b)
			total += n
			b = b[n:]
			if err != nil {
				return total, err
			}
			if n == 0 {
				return total, io.ErrNoProgress
			}
		}
	}
	return total, nil
}

func Read(r io.Reader, logger *JSONLLogger) (Message, uint64, error) {
	br := bufio.NewReader(r)
	prefix := make([]byte, 8)
	n, err := io.ReadFull(br, prefix)
	if err != nil {
		return Message{}, uint64(n), err
	}
	bodyLen := binary.BigEndian.Uint64(prefix)
	if bodyLen < 4 || bodyLen > MaxFrameBytes {
		return Message{}, uint64(n), fmt.Errorf("invalid body length %d", bodyLen)
	}
	body := make([]byte, bodyLen)
	m, err := io.ReadFull(br, body)
	n += m
	if err != nil {
		return Message{}, uint64(n), err
	}
	hlen := int(binary.BigEndian.Uint32(body[:4]))
	if hlen <= 0 || 4+hLenSafe(hlen) > len(body) {
		return Message{}, uint64(n), errors.New("invalid header length")
	}
	var h Header
	if err := json.Unmarshal(body[4:4+hlen], &h); err != nil {
		return Message{}, uint64(n), err
	}
	payload := append([]byte(nil), body[4+hlen:]...)
	if h.ProtocolVersion != Version || h.PayloadBytes != uint64(len(payload)) {
		return Message{}, uint64(n), errors.New("header/version/payload mismatch")
	}
	hash := sha256.Sum256(payload)
	if h.PayloadSHA256 != hex.EncodeToString(hash[:]) {
		return Message{}, uint64(n), errors.New("payload digest mismatch")
	}
	serialized := uint64(n)
	if logger != nil {
		_ = logger.Log(LogRecord{h, "received", serialized, serialized})
	}
	return Message{h, payload}, serialized, nil
}

func hLenSafe(v int) int { return v }
