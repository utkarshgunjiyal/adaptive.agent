# Test credentials — Runner.ai

## Demo user (auto-registered during smoke test)
- Email: `demo@runner.ai`
- Password: `demo1234`
- Name: Demo Researcher

## Auth endpoints
- POST `/api/auth/register` — { email, password, name }
- POST `/api/auth/login` — { email, password } → { access_token, user }
- GET `/api/auth/me` — Bearer required
- POST `/api/auth/logout` — Bearer required

All authenticated endpoints require `Authorization: Bearer <token>`.

## Sample flow
1. Register or login → capture `access_token`.
2. `POST /api/documents/upload` (multipart PDF) → document + job ids.
3. Poll `GET /api/documents/{document_id}` until `status == "ready"`.
4. `POST /api/agent/run/stream` with `{ message, document_ids: [...] }` — SSE stream.
