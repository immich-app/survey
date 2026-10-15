export function toClientSurvey<T extends { password_hash?: string | null }>(
  survey: T,
): Omit<T, 'password_hash'> & { has_password: boolean } {
  const { password_hash, ...rest } = survey;
  return { ...rest, has_password: !!password_hash };
}

/** What respondents may see of a survey: never the password hash or who created it. */
export function toPublicSurvey<T extends { password_hash?: string | null; created_by?: string | null }>(
  survey: T,
): Omit<T, 'password_hash' | 'created_by'> {
  const { password_hash: _password_hash, created_by: _created_by, ...rest } = survey;
  return rest;
}
