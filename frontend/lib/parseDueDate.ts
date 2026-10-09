/**
 * Parse a YYYY-MM-DD due date as a date on the user's own calendar.
 *
 * `new Date('2026-07-17')` is parsed as UTC midnight, which in any timezone
 * behind UTC is still the 16th locally - so rendering it shows the wrong day.
 * Constructing from the parts gives local midnight instead.
 *
 * Lives here rather than in a page module because more than one page renders
 * due dates, and importing a helper from a page drags that page's whole
 * component tree along with it.
 */
export function parseDueDate(dateStr: string): Date {
  const [year, month, day] = dateStr.split('-').map(Number)
  return new Date(year, month - 1, day)
}
