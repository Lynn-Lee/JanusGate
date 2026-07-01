import { describe, expect, it } from 'vitest';
import { parseApiError } from './client';

describe('parseApiError', () => {
  it('prefers Phase 3 ErrorResponse message and keeps request id', async () => {
    const response = new Response(
      JSON.stringify({
        code: 'SELF_APPROVAL_NOT_ALLOWED',
        message: '不能审批自己的申请',
        detail: 'SELF_APPROVAL_NOT_ALLOWED',
        request_id: 'req-1'
      }),
      { status: 403, headers: { 'Content-Type': 'application/json' } }
    );

    await expect(parseApiError(response)).resolves.toMatchObject({
      code: 'SELF_APPROVAL_NOT_ALLOWED',
      message: '不能审批自己的申请',
      requestId: 'req-1',
      status: 403
    });
  });

  it('maps legacy FastAPI detail string into stable message', async () => {
    const response = new Response(JSON.stringify({ detail: '用户名或密码错误' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' }
    });

    await expect(parseApiError(response)).resolves.toMatchObject({
      code: 'UNAUTHORIZED',
      message: '用户名或密码错误',
      status: 401
    });
  });
});
