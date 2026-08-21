package metrics

import (
	"encoding/csv"
	"errors"
	"os"
	"strconv"
	"sync"
	"time"
)

type Operation struct {
	RunID, Configuration  string
	Round                 int
	Role, Operation       string
	SemanticallyNecessary bool
	TimestampUTC          string
}

type Trace struct {
	mu            sync.Mutex
	f             *os.File
	csv           *csv.Writer
	configuration string
}

func NewTrace(path, runID, configuration string) (*Trace, error) {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return nil, err
	}
	w := csv.NewWriter(f)
	if err = w.Write([]string{"run_id", "configuration", "round_id", "role", "operation", "semantically_necessary_learning", "timestamp_utc"}); err != nil {
		f.Close()
		return nil, err
	}
	w.Flush()
	if err = w.Error(); err != nil {
		f.Close()
		return nil, err
	}
	return &Trace{f: f, csv: w, configuration: configuration}, nil
}
func (t *Trace) Record(o Operation) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if o.TimestampUTC == "" {
		o.TimestampUTC = time.Now().UTC().Format(time.RFC3339Nano)
	}
	if err := t.csv.Write([]string{o.RunID, o.Configuration, strconv.Itoa(o.Round), o.Role, o.Operation, strconv.FormatBool(o.SemanticallyNecessary), o.TimestampUTC}); err != nil {
		return err
	}
	t.csv.Flush()
	if err := t.csv.Error(); err != nil {
		return err
	}
	return t.f.Sync()
}
func (t *Trace) Close() error {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.csv.Flush()
	if err := t.csv.Error(); err != nil {
		return err
	}
	return t.f.Close()
}

func ValidateTypeI(configuration string, ops []Operation) error {
	if configuration != "I-P" && configuration != "I-OR" && configuration != "I-ER" {
		return nil
	}
	for _, o := range ops {
		if o.Role == "O_1" && o.SemanticallyNecessary {
			return errors.New("Type-I violation: semantically necessary learning at O_1: " + o.Operation)
		}
	}
	return nil
}
