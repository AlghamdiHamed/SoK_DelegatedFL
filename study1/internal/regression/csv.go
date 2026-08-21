package regression

import (
	"encoding/csv"
	"errors"
	"os"
	"strconv"
	"strings"
)

// ReadFrozenCSV reads only x_* fields and target_heart_rate. Metadata never enters X.
func ReadFrozenCSV(path, owner, partition string) (Dataset, error) {
	f, e := os.Open(path)
	if e != nil {
		return Dataset{}, e
	}
	defer f.Close()
	r := csv.NewReader(f)
	head, e := r.Read()
	if e != nil {
		return Dataset{}, e
	}
	xcols := make([]int, 0, Dimension)
	target := -1
	ownerCol, partCol := -1, -1
	for i, n := range head {
		if strings.HasPrefix(n, "x_") {
			xcols = append(xcols, i)
		}
		if n == "target_heart_rate" {
			target = i
		}
		if n == "owner" {
			ownerCol = i
		}
		if n == "partition" {
			partCol = i
		}
	}
	if len(xcols) != Dimension || target < 0 || ownerCol < 0 || partCol < 0 {
		return Dataset{}, errors.New("unexpected frozen Study-I schema")
	}
	var d Dataset
	for {
		row, e := r.Read()
		if e != nil {
			if e.Error() == "EOF" {
				break
			}
			return d, e
		}
		if row[ownerCol] != owner || row[partCol] != partition {
			continue
		}
		x := make([]float64, Dimension)
		for j, c := range xcols {
			x[j], e = strconv.ParseFloat(row[c], 64)
			if e != nil {
				return d, e
			}
		}
		y, e := strconv.ParseFloat(row[target], 64)
		if e != nil {
			return d, e
		}
		d.X = append(d.X, x)
		d.Y = append(d.Y, y)
	}
	return d, d.Validate()
}
