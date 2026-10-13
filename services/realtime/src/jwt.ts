// Access token checks, behaving like tpsarena_common.jwt_auth (Python): EdDSA only, exp and sub
// required, sub a UUID; keys from Auth's JWKS (cached, fetched again for an unknown kid).
import { createRemoteJWKSet, errors, jwtVerify, type JWTVerifyGetKey } from "jose";

export type TokenErrorCode = "not_authenticated" | "token_expired" | "invalid_token";

export class TokenError extends Error {
  constructor(
    readonly code: TokenErrorCode,
    message: string,
  ) {
    super(message);
  }
}

export interface VerifiedToken {
  userId: string;
  expiresAt: number; // ms
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type Verifier = (token: string) => Promise<VerifiedToken>;

export function remoteKeys(jwksUrl: string): JWTVerifyGetKey {
  return createRemoteJWKSet(new URL(jwksUrl), { cooldownDuration: 30_000, timeoutDuration: 5_000 });
}

export function makeVerifier(keys: JWTVerifyGetKey): Verifier {
  return async (token) => {
    if (!token) throw new TokenError("not_authenticated", "Missing access token.");
    try {
      const { payload } = await jwtVerify(token, keys, { algorithms: ["EdDSA"], requiredClaims: ["exp", "sub"] });
      if (!UUID.test(payload.sub ?? "")) throw new TokenError("invalid_token", "The access token is invalid.");
      return { userId: payload.sub!.toLowerCase(), expiresAt: payload.exp! * 1000 };
    } catch (error) {
      if (error instanceof TokenError) throw error;
      if (error instanceof errors.JWTExpired) throw new TokenError("token_expired", "The access token has expired.");
      throw new TokenError("invalid_token", "The access token is invalid.");
    }
  };
}
