import assert from 'node:assert/strict'
import { invNorm, expectedMaxSharpe } from './stats.ts'

assert.ok(Math.abs(invNorm(0.975) - 1.959964) < 1e-5)
assert.ok(Math.abs(invNorm(0.001) + 3.090232) < 1e-5)
assert.equal(expectedMaxSharpe(1), 0)
for (const n of [10, 100, 1000, 10000]) console.log(n, expectedMaxSharpe(n).toFixed(2))
assert.ok(expectedMaxSharpe(1000) > expectedMaxSharpe(100))
console.log('stats ok')
