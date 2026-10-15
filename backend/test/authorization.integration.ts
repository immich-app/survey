import { describe, expect, it, beforeAll } from 'vitest';
import { request, createCookieForRole } from './helpers';

async function createSurvey(cookie: string): Promise<{ id: string }> {
  const res = await request('/api/surveys', {
    method: 'POST',
    body: JSON.stringify({ title: `Auth Test ${Date.now()}` }),
    cookie,
  });
  return (await res.json()) as { id: string };
}

describe('Authorization - unauthenticated requests', () => {
  it('rejects unauthenticated GET /api/surveys with 401', async () => {
    const res = await request('/api/surveys');
    expect(res.status).toBe(401);
  });

  it('rejects unauthenticated POST /api/surveys with 401', async () => {
    const res = await request('/api/surveys', {
      method: 'POST',
      body: JSON.stringify({ title: 'Should Fail' }),
    });
    expect(res.status).toBe(401);
  });

  it('rejects unauthenticated GET /api/tags with 401', async () => {
    const res = await request('/api/tags');
    expect(res.status).toBe(401);
  });

  it('rejects unauthenticated POST /api/tags with 401', async () => {
    const res = await request('/api/tags', {
      method: 'POST',
      body: JSON.stringify({ name: 'Should Fail' }),
    });
    expect(res.status).toBe(401);
  });
});

describe('Authorization - public routes', () => {
  it('allows unauthenticated GET /api/auth/me', async () => {
    const res = await request('/api/auth/me');
    expect(res.status).toBe(200);
  });

  it('allows unauthenticated GET /api/s/:slug (returns 404 for missing slug, not 401)', async () => {
    const res = await request('/api/s/nonexistent-slug');
    expect(res.status).not.toBe(401);
  });
});

describe('Authorization - viewer role', () => {
  let viewerCookie: string;

  beforeAll(async () => {
    viewerCookie = await createCookieForRole('viewer');
  });

  it('can GET /api/surveys', async () => {
    const res = await request('/api/surveys', { cookie: viewerCookie });
    expect(res.status).toBe(200);
  });

  it('can GET /api/tags', async () => {
    const res = await request('/api/tags', { cookie: viewerCookie });
    expect(res.status).toBe(200);
  });

  it('cannot POST /api/surveys (403)', async () => {
    const res = await request('/api/surveys', {
      method: 'POST',
      body: JSON.stringify({ title: 'Viewer Survey' }),
      cookie: viewerCookie,
    });
    expect(res.status).toBe(403);
  });

  it('cannot POST /api/tags (403)', async () => {
    const res = await request('/api/tags', {
      method: 'POST',
      body: JSON.stringify({ name: 'Viewer Tag' }),
      cookie: viewerCookie,
    });
    expect(res.status).toBe(403);
  });
});

describe('Authorization - editor role', () => {
  let editorCookie: string;
  let surveyId: string;
  let tagId: string;

  beforeAll(async () => {
    editorCookie = await createCookieForRole('editor');
    surveyId = (await createSurvey(editorCookie)).id;

    const tagRes = await request('/api/tags', {
      method: 'POST',
      body: JSON.stringify({ name: `Editor Auth Tag ${Date.now()}` }),
      cookie: editorCookie,
    });
    const tag = (await tagRes.json()) as { id: string };
    tagId = tag.id;
  });

  it('can GET /api/surveys', async () => {
    const res = await request('/api/surveys', { cookie: editorCookie });
    expect(res.status).toBe(200);
  });

  it('can POST /api/surveys (201)', async () => {
    const res = await request('/api/surveys', {
      method: 'POST',
      body: JSON.stringify({ title: `Editor Create ${Date.now()}` }),
      cookie: editorCookie,
    });
    expect(res.status).toBe(201);
  });

  it('can POST /api/tags (201)', async () => {
    const res = await request('/api/tags', {
      method: 'POST',
      body: JSON.stringify({ name: `Editor Tag ${Date.now()}` }),
      cookie: editorCookie,
    });
    expect(res.status).toBe(201);
  });

  it('can DELETE a survey they created', async () => {
    const res = await request(`/api/surveys/${surveyId}`, {
      method: 'DELETE',
      cookie: editorCookie,
    });
    expect(res.status).toBe(204);
  });

  it("cannot DELETE another editor's survey (403)", async () => {
    const otherEditorCookie = await createCookieForRole('editor', { sub: 'test-other-editor' });
    const { id } = await createSurvey(otherEditorCookie);

    const res = await request(`/api/surveys/${id}`, {
      method: 'DELETE',
      cookie: editorCookie,
    });
    expect(res.status).toBe(403);
  });

  it('can DELETE a survey they duplicated from someone else', async () => {
    const adminCookie = await createCookieForRole('admin');
    const { id: sourceId } = await createSurvey(adminCookie);
    const dupRes = await request(`/api/surveys/${sourceId}/duplicate`, {
      method: 'POST',
      cookie: editorCookie,
    });
    const { survey } = (await dupRes.json()) as { survey: { id: string } };

    const res = await request(`/api/surveys/${survey.id}`, {
      method: 'DELETE',
      cookie: editorCookie,
    });
    expect(res.status).toBe(204);
  });

  it('can DELETE a survey they imported', async () => {
    const importRes = await request('/api/surveys/import', {
      method: 'POST',
      body: JSON.stringify({ version: 1, title: `Editor Import ${Date.now()}`, sections: [{ title: 'Section' }] }),
      cookie: editorCookie,
    });
    const { survey } = (await importRes.json()) as { survey: { id: string } };

    const res = await request(`/api/surveys/${survey.id}`, {
      method: 'DELETE',
      cookie: editorCookie,
    });
    expect(res.status).toBe(204);
  });

  it('cannot DELETE a survey they created after being demoted to viewer (403)', async () => {
    const { id } = await createSurvey(editorCookie);
    const demotedCookie = await createCookieForRole('viewer', { sub: 'test-editor' });

    const res = await request(`/api/surveys/${id}`, {
      method: 'DELETE',
      cookie: demotedCookie,
    });
    expect(res.status).toBe(403);
  });

  it('cannot DELETE /api/tags/:id (403)', async () => {
    const res = await request(`/api/tags/${tagId}`, {
      method: 'DELETE',
      cookie: editorCookie,
    });
    expect(res.status).toBe(403);
  });
});

describe('Authorization - admin role', () => {
  let adminCookie: string;

  beforeAll(async () => {
    adminCookie = await createCookieForRole('admin');
  });

  it('can GET /api/surveys', async () => {
    const res = await request('/api/surveys', { cookie: adminCookie });
    expect(res.status).toBe(200);
  });

  it('can POST /api/surveys (201)', async () => {
    const res = await request('/api/surveys', {
      method: 'POST',
      body: JSON.stringify({ title: `Admin Create ${Date.now()}` }),
      cookie: adminCookie,
    });
    expect(res.status).toBe(201);
  });

  it("can DELETE another user's survey", async () => {
    const editorCookie = await createCookieForRole('editor');
    const { id } = await createSurvey(editorCookie);

    const res = await request(`/api/surveys/${id}`, {
      method: 'DELETE',
      cookie: adminCookie,
    });
    expect(res.status).toBe(204);
  });

  it('can POST /api/tags (201)', async () => {
    const res = await request('/api/tags', {
      method: 'POST',
      body: JSON.stringify({ name: `Admin Tag ${Date.now()}` }),
      cookie: adminCookie,
    });
    expect(res.status).toBe(201);
  });

  it('can DELETE /api/tags/:id', async () => {
    const createRes = await request('/api/tags', {
      method: 'POST',
      body: JSON.stringify({ name: `Admin Delete Tag ${Date.now()}` }),
      cookie: adminCookie,
    });
    const { id } = (await createRes.json()) as { id: string };

    const res = await request(`/api/tags/${id}`, {
      method: 'DELETE',
      cookie: adminCookie,
    });
    expect(res.status).toBe(204);
  });
});
