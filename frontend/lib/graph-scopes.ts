export const GRAPH_RESOURCE_SCOPES = [
  'User.Read',
  'Mail.Read',
  'Chat.Read',
  'Calendars.ReadWrite',
  'OnlineMeetings.ReadWrite',
]

export const OAUTH_SCOPES = `openid email profile offline_access ${GRAPH_RESOURCE_SCOPES.join(' ')}`

/**
 * Google scopes. Calendar is included because scheduling has to see every
 * calendar the user keeps - suggesting a slot while they are booked elsewhere
 * is worse than suggesting nothing.
 */
export const GOOGLE_RESOURCE_SCOPES = [
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/calendar',
]

/**
 * Google issues a refresh token only when BOTH of these are sent. Without them
 * the connection works for about an hour and then dies with no way to recover
 * it short of reconnecting by hand.
 */
export const GOOGLE_OAUTH_QUERY_PARAMS = {
  access_type: 'offline',
  prompt: 'consent',
}
