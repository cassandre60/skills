package mgoreci

import "testing"

func TestClamp(t *testing.T) {
	cases := []struct {
		name      string
		v, lo, hi int
		want      int
	}{
		{"in range", 5, 0, 10, 5},
		{"below lo", -3, 0, 10, 0},
		{"above hi", 15, 0, 10, 10},
	}
	for _, tc := range cases {
		if got := Clamp(tc.v, tc.lo, tc.hi); got != tc.want {
			t.Errorf("%s: Clamp(%d, %d, %d) = %d; want %d",
				tc.name, tc.v, tc.lo, tc.hi, got, tc.want)
		}
	}
}
