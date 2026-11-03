import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildReviewImageUrl,
  formatCategory,
  formatMetric,
  reviewActionFor,
} from './reviewDashboard.js';

test('review image URLs stay under the protected proxy path', () => {
  assert.equal(
    buildReviewImageUrl('images/2026/10/21/request-1.jpg'),
    '/api/review/images/images/2026/10/21/request-1.jpg',
  );
  assert.equal(buildReviewImageUrl('../secret.jpg'), null);
  assert.equal(buildReviewImageUrl(''), null);
});

test('review helpers preserve canonical paper category and empty metrics', () => {
  assert.equal(formatCategory('paper'), 'paper');
  assert.equal(formatCategory('pet_bottle'), 'pet bottle');
  assert.equal(formatMetric(null), '—');
  assert.equal(formatMetric(0.875), '0.88');
  assert.equal(reviewActionFor('paper', 'cardboard'), 'correct');
  assert.equal(reviewActionFor('paper', 'paper'), 'confirm');
});
