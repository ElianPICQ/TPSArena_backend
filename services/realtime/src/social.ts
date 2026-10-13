import { TokenError } from "./jwt.js";

export class SocialUnavailable extends Error {}

/** A player's friends, asked from Social with the player's own token (only friends see presence). */
export async function fetchFriends(socialUrl: string, token: string): Promise<string[]> {
  let response: Response;
  try {
    response = await fetch(`${socialUrl}/friends`, {
      headers: { authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(5000),
    });
  } catch (error) {
    throw new SocialUnavailable(String(error));
  }
  if (response.status === 401) throw new TokenError("invalid_token", "The access token is invalid.");
  if (!response.ok) throw new SocialUnavailable(`Social answered ${response.status}`);
  const body = (await response.json()) as { friends: { user_id: string }[] };
  return body.friends.map((f) => f.user_id);
}
