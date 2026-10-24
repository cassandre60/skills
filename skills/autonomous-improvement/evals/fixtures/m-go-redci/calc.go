package mgoreci

// Clamp constrains v to the inclusive range [lo, hi].
func Clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return lo
	}
	return v
}
