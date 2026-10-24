/** Verify streaming microphone resampling fidelity, anti-alias rejection, and finite tail ownership. */

import assert from 'node:assert/strict'
import test from 'node:test'

import { StreamingLinearResampler } from '../src/voice/audio-capture.ts'

const TARGET_RATE = 16_000
const DEVICE_RATES = [44_100, 48_000, 96_000]

function sineSamples(rate, frequency, length = rate, amplitude = 0.5) {
  return Float32Array.from({ length }, (_, index) => amplitude * Math.sin(2 * Math.PI * frequency * index / rate))
}

function joinSamples(chunks) {
  const joined = new Float32Array(chunks.reduce((length, chunk) => length + chunk.length, 0))
  let offset = 0
  for (const chunk of chunks) {
    joined.set(chunk, offset)
    offset += chunk.length
  }
  return joined
}

function rms(samples) {
  return Math.sqrt(samples.reduce((total, sample) => total + sample * sample, 0) / samples.length)
}

// Exclude startup and stream-end transients; the central 0.8 seconds contains
// whole cycles for every tested tone, so phase delay cannot bias gain checks.
function steadyState(samples) {
  return samples.subarray(TARGET_RATE / 10, TARGET_RATE * 9 / 10)
}

function toneAmplitude(samples, frequency) {
  let sine = 0
  let cosine = 0
  for (let index = 0; index < samples.length; index += 1) {
    const phase = 2 * Math.PI * frequency * index / TARGET_RATE
    sine += samples[index] * Math.sin(phase)
    cosine += samples[index] * Math.cos(phase)
  }
  return 2 * Math.hypot(sine, cosine) / samples.length
}

test('16 kHz capture remains sample-exact across arbitrary chunks and adds no tail', () => {
  const input = Float32Array.from({ length: 1_153 }, (_, index) => Math.sin(index * 0.071) * Math.cos(index * 0.003))
  input.set([-1, -0.5, -0, 0, 0.5, 1])
  const resampler = new StreamingLinearResampler(TARGET_RATE)
  const actual = joinSamples([
    resampler.process(input.subarray(0, 1)),
    resampler.process(input.subarray(1, 319)),
    resampler.process(new Float32Array()),
    resampler.process(input.subarray(319)),
    resampler.flush(),
  ])
  assert.equal(resampler.inputSampleRate, TARGET_RATE)
  assert.deepEqual(actual, input)
  assert.equal(resampler.flush().length, 0)
})

for (const rate of DEVICE_RATES) {
  test(`${rate} Hz keeps speech-band tones and rejects 10/12 kHz aliases`, () => {
    for (const frequency of [1_000, 4_000, 6_000]) {
      const resampler = new StreamingLinearResampler(rate)
      const output = steadyState(resampler.process(sineSamples(rate, frequency)))
      const amplitude = toneAmplitude(output, frequency)
      // Linear interpolation at 44.1 kHz slightly attenuates the upper speech
      // band. Keep it within 1 dB while checking energy stays at the input tone.
      assert.ok(amplitude > 0.5 * 0.89 && amplitude < 0.5 * 1.02, `${frequency} Hz amplitude at ${rate}: ${amplitude}`)
      const residual = Math.sqrt(Math.max(0, rms(output) ** 2 - amplitude ** 2 / 2))
      assert.ok(residual < 0.025, `${frequency} Hz distortion at ${rate}: ${residual}`)
    }
    for (const frequency of [10_000, 12_000]) {
      const output = steadyState(new StreamingLinearResampler(rate).process(sineSamples(rate, frequency)))
      const gain = rms(output) / (0.5 / Math.sqrt(2))
      assert.ok(gain < 0.001, `${frequency} Hz aliased gain at ${rate}: ${20 * Math.log10(gain)} dB`)
    }
    const dc = steadyState(new StreamingLinearResampler(rate).process(new Float32Array(rate).fill(0.25)))
    assert.ok(dc.every(sample => Math.abs(sample - 0.25) < 1e-6), 'DC gain must remain one')
  })

  test(`${rate} Hz produces identical samples across single-sample and irregular chunk boundaries`, () => {
    const input = Float32Array.from({ length: 7_919 }, (_, index) => (
      0.2 * Math.sin(2 * Math.PI * 317 * index / rate)
      + 0.1 * Math.sin(2 * Math.PI * 4_321 * index / rate)
    ))
    const continuous = new StreamingLinearResampler(rate)
    const expected = joinSamples([continuous.process(input), continuous.flush()])
    const chunked = new StreamingLinearResampler(rate)
    const chunks = []
    const sizes = [1, 17, 320, 3, 1_024, 2, 147]
    for (let offset = 0, chunk = 0; offset < input.length; chunk += 1) {
      const end = Math.min(input.length, offset + sizes[chunk % sizes.length])
      chunks.push(chunked.process(input.subarray(offset, end)))
      chunks.push(chunked.process(new Float32Array()))
      offset = end
    }
    chunks.push(chunked.flush())
    assert.deepEqual(joinSamples(chunks), expected)
    // The finite filter tail must remain below two milliseconds at supported
    // device rates, so it cannot meaningfully extend endpoint timing.
    assert.ok(expected.length >= Math.floor(input.length * TARGET_RATE / rate))
    assert.ok(expected.length <= Math.ceil(input.length * TARGET_RATE / rate) + 32)
  })
}

test('flush preserves a final impulse once, and reset clears filter history and sample phase', () => {
  for (const rate of DEVICE_RATES) {
    const input = new Float32Array(997)
    input[input.length - 1] = 1
    const resampler = new StreamingLinearResampler(rate)
    const head = resampler.process(input)
    const tail = resampler.flush()
    assert.ok(tail.some(sample => Math.abs(sample) > 0.01), `${rate}: final sample was dropped`)
    assert.equal(resampler.flush().length, 0)
    assert.throws(() => resampler.process(Float32Array.of(0)), /reset/)
    // Compare with an independently continued stream, using generous silence
    // rather than reproducing the filter's private coefficient/tap formula.
    const continued = new StreamingLinearResampler(rate)
    const reference = joinSamples([continued.process(input), continued.process(new Float32Array(rate / 100))])
    const drained = joinSamples([head, tail])
    assert.deepEqual(drained, reference.subarray(0, drained.length))
    assert.ok(reference.subarray(drained.length).every(sample => Math.abs(sample) < 1e-10))

    resampler.reset()
    const fresh = new StreamingLinearResampler(rate)
    const next = sineSamples(rate, 723, 1_019, 0.2)
    assert.deepEqual(resampler.process(next), fresh.process(next))
    assert.deepEqual(resampler.flush(), fresh.flush())

    resampler.reset()
    assert.equal(resampler.flush().length, 0)
    assert.equal(resampler.flush().length, 0)
  }
})
