/** Base Payment: the levers BRAIN's base-payment articles name, read off your own submissions. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

export const BASE_PAYMENT_KEY = ['tools', 'base-payment'] as const

export interface Quantity {
  today: string
  submittedToday: number
  alphaCap: number
  alphaRemaining: number
  superToday: number
  superCap: number
  d0Month: number
  d0Cap: number
  d0Remaining: number
  streak: number
  lastSevenDays: number
  recent: { date: string; count: number }[]
}

export interface Share {
  name: string
  count: number
  share: number
  over: boolean
}

export interface Diversity {
  windowDays: number
  considered: number
  axes: Record<string, Share[]>
  flags: string[]
}

export interface GrowthBlock {
  count: number
  sharpe: number | null
  prodCorrelation: number | null
  selfCorrelation: number | null
  turnover: number | null
  margin: number | null
}

export interface Growth {
  region: string
  recent: GrowthBlock
  previous: GrowthBlock
  trend: 'improving' | 'flat' | 'declining' | 'new'
}

export interface OsmosisAlpha {
  alphaId: string
  points: number
  sharpe: number | null
  turnover: number | null
  margin: number | null
  note: string
}

export interface OsmosisScope {
  scope: string
  submitted: number
  ready: boolean
  needed: number
  alphas: OsmosisAlpha[]
  dropped: { alphaId: string; reason: string }[]
}

export interface Osmosis {
  scopes: OsmosisScope[]
  readyScopes: number
  requiredScopes: number
  valid: boolean
  pointsPerScope: number
  minAlphas: number
  nextLock: string
}

export interface Pick {
  alphaId: string
  brainUrl: string | null
  scope: string
  reasons: string[]
  score: number
}

export interface Plan {
  slots: number
  picks: Pick[]
  short: number
  tips: string[]
  superAlphaOpen: boolean
}

export interface ChecklistItem {
  label: string
  status: 'ok' | 'warn' | 'todo'
  detail: string
}

export interface BasePaymentReport {
  quantity: Quantity
  diversity: Diversity
  growth: Growth[]
  osmosis: Osmosis
  plan: Plan
  checklist: ChecklistItem[]
  multipliersKnown: boolean
}

export const basePayment = {
  /** Read-only: spends no simulation. */
  report: () => http.get<BasePaymentReport>('/api/tools/base-payment'),
  /** Queues rank(Alpha) and sign(Alpha) at the Alpha's own settings: two simulations. */
  robustness: (alphaId: string) =>
    http.post<components['schemas']['AddedTask']>('/api/tools/base-payment/robustness', {
      alphaId,
    }),
}

/** The Osmosis proposal as the CSV to copy into BRAIN's Submitted Alphas table. */
export function osmosisCsv(osmosis: Osmosis): string {
  const lines = ['scope,alpha_id,points']
  for (const scope of osmosis.scopes) {
    for (const a of scope.alphas) lines.push(`${scope.scope},${a.alphaId},${a.points}`)
  }
  return `${lines.join('\n')}\n`
}
