import assert from 'node:assert/strict';
import test from 'node:test';
import { measureCatalogColumnCount } from '../src/lib/catalogGridMeasurement.ts';

test('zero-width and below-minimum one-track reads are unmeasured', () => {
  const measurements = [0, 60].map((gridWidth) => measureCatalogColumnCount({
    gridTemplateColumns: '140px',
    gridWidth,
    minColumnWidth: 140,
  }));

  assert.deepEqual(measurements, [null, null]);
});

test('a laid-out follow-up self-heals an initially unmeasured grid', () => {
  const nextFrame = measureCatalogColumnCount({
    gridTemplateColumns: '164px 164px 164px 164px 164px',
    gridWidth: 900,
    minColumnWidth: 140,
  });

  assert.equal(nextFrame, 5);
});

test('resolved healthy templates preserve five and eight track layouts', () => {
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '184px 184px 184px 184px 184px',
    gridWidth: 1000,
    minColumnWidth: 180,
  }), 5);
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '162px 162px 162px 162px 162px 162px 162px 162px',
    gridWidth: 1440,
    minColumnWidth: 140,
  }), 8);
});

test('a real one-column grid at the minimum width remains valid', () => {
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '140px',
    gridWidth: 140,
    minColumnWidth: 140,
  }), 1);
});

test('fixed mobile tracks use their zero CSS minimum after the breakpoint override', () => {
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '30px 30px',
    gridWidth: 60,
    minColumnWidth: 0,
  }), 2);
});
