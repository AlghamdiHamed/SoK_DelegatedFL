package metrics

import (
	"bufio"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

type Sample struct {
	TimestampUTC string `json:"timestamp_utc"`
	RSSBytes     uint64 `json:"rss_bytes"`
}
type Monitor struct {
	mu      sync.Mutex
	samples []Sample
	stop    chan struct{}
	done    chan struct{}
}

func NewMonitor(period time.Duration) *Monitor {
	m := &Monitor{stop: make(chan struct{}), done: make(chan struct{})}
	go func() {
		defer close(m.done)
		ticker := time.NewTicker(period)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				m.take()
			case <-m.stop:
				m.take()
				return
			}
		}
	}()
	return m
}
func (m *Monitor) take() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.samples = append(m.samples, Sample{time.Now().UTC().Format(time.RFC3339Nano), currentRSS()})
}
func (m *Monitor) Stop() []Sample {
	close(m.stop)
	<-m.done
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]Sample(nil), m.samples...)
}
func currentRSS() uint64 {
	f, e := os.Open("/proc/self/status")
	if e != nil {
		return 0
	}
	defer f.Close()
	s := bufio.NewScanner(f)
	for s.Scan() {
		p := strings.Fields(s.Text())
		if len(p) >= 2 && p[0] == "VmRSS:" {
			v, _ := strconv.ParseUint(p[1], 10, 64)
			return v * 1024
		}
	}
	return 0
}
func PeakRSSBytes() uint64 {
	var r syscall.Rusage
	if syscall.Getrusage(syscall.RUSAGE_SELF, &r) != nil {
		return 0
	}
	if runtime.GOOS == "linux" {
		return uint64(r.Maxrss) * 1024
	}
	return uint64(r.Maxrss)
}
func ProcessCPUTimeNS() int64 {
	var r syscall.Rusage
	if syscall.Getrusage(syscall.RUSAGE_SELF, &r) != nil {
		return 0
	}
	return r.Utime.Sec*1e9 + r.Utime.Usec*1e3 + r.Stime.Sec*1e9 + r.Stime.Usec*1e3
}
