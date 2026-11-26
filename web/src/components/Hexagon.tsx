import type { ReactNode } from 'react'
import './Hexagon.css'

type HexagonProps = {
  children: ReactNode
  /** Smaller text for labels longer than two characters ("10k+", "OLX"). */
  compact?: boolean
}

/**
 * Red hexagon used for badges and step numbers (echoes the hex nut in the
 * logo). It is 64 × 72 px on wide screens and 40 × 46 px on phones.
 */
export function Hexagon({ children, compact = false }: HexagonProps) {
  return <span className={compact ? 'hexagon hexagon--compact' : 'hexagon'}>{children}</span>
}
