import { describe, expect, it } from 'vitest';
import { canDeleteSurvey } from '$shared/survey-permissions';

describe('canDeleteSurvey', () => {
  it.each([
    { role: 'admin', createdBy: 'other', expected: true },
    { role: 'admin', createdBy: null, expected: true },
    { role: 'editor', createdBy: 'me', expected: true },
    { role: 'editor', createdBy: 'other', expected: false },
    { role: 'editor', createdBy: null, expected: false },
    { role: 'viewer', createdBy: 'me', expected: false },
  ] as const)(
    '$role deleting a survey created by $createdBy is allowed: $expected',
    ({ role, createdBy, expected }) => {
      expect(canDeleteSurvey({ sub: 'me', role }, createdBy)).toBe(expected);
    },
  );
});
