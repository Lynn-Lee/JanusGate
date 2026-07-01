import { describe, expect, it } from 'vitest';

import { safeMetadata } from './AuditsPage';

describe('safeMetadata', () => {
  it('redacts backend sensitive metadata keys in audit details', () => {
    const rendered = safeMetadata({
      authorization: 'Bearer plaintext',
      cookie: 'sid=plaintext',
      credential: 'plaintext-credential',
      credentials: { password: 'nested-password' },
      ssh_key: '-----BEGIN PRIVATE KEY-----',
      nested: { refresh_token: 'refresh-plaintext', allowed: 'visible' }
    });

    expect(rendered).toContain('******');
    expect(rendered).toContain('visible');
    expect(rendered).not.toContain('Bearer plaintext');
    expect(rendered).not.toContain('sid=plaintext');
    expect(rendered).not.toContain('plaintext-credential');
    expect(rendered).not.toContain('nested-password');
    expect(rendered).not.toContain('-----BEGIN PRIVATE KEY-----');
    expect(rendered).not.toContain('refresh-plaintext');
  });
});
