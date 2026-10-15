/**
 * Survey permission rules shared by the server, which enforces them, and the
 * dashboard, which hides actions the user can't take — a change here changes
 * both at once.
 */

/** Admins can delete any survey; editors only the ones they created. */
export function canDeleteSurvey(
  user: { sub: string; role: 'admin' | 'editor' | 'viewer' },
  createdBy: string | null,
): boolean {
  if (user.role === 'admin') return true;

  return user.role === 'editor' && createdBy === user.sub;
}
