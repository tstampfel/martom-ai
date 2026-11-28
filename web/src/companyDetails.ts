import { copy } from './copy'
import details from './companyDetails.json'

/**
 * Martom's legal details for the footer of every page: the full company
 * name, address, JIB (13-digit tax id) and, if Martom is VAT-registered, the
 * PDV number (12 digits).
 *
 * The values come only from Tomislav or Toni, through the design canvas.
 * Never guess them and never put in a value that only looks real: the empty
 * strings and `vatRegistered: null` in companyDetails.json are the "not filled
 * in yet" state. `npm run check:launch` (web/scripts/check-launch.mjs) fails
 * until they are all filled in and in the build.
 */
export type CompanyDetails = {
  legalName: string
  address: string
  jib: string
  /** null until Tomislav or Toni say whether Martom is VAT-registered. */
  vatRegistered: boolean | null
  vatNumber: string
}

export const companyDetails: CompanyDetails = details

/**
 * The footer line, or null while the name, address or JIB is still empty, so
 * the footer shows nothing half filled in. The labels are in
 * `copy.footer.company`.
 */
export function companyLine(company: CompanyDetails): string | null {
  const { legalName, address, jib, vatRegistered, vatNumber } = company
  if (!legalName || !address || !jib) return null
  const { jibLabel, vatLabel, separator } = copy.footer.company
  const parts = [legalName, address, `${jibLabel} ${jib}`]
  if (vatRegistered && vatNumber) parts.push(`${vatLabel} ${vatNumber}`)
  return parts.join(separator)
}
