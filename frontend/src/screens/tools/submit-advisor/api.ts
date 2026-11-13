/** Submit Advisor: which Alphas from the Tasks to submit, ranked, with the reason for each. */

import type { components } from '@/api/generated'
import { http } from '@/api/http'

type Schemas = components['schemas']

export type SubmitAdvice = Schemas['SubmitAdvice']
export type AdvisedAlpha = Schemas['AdvisedAlpha']
export type AdviceStatus = AdvisedAlpha['status']

export const ADVICE_KEY = ['tools', 'submit-advisor'] as const

export const submitAdvisor = {
  /** Read-only: spends no simulation and asks BRAIN nothing. */
  advice: () => http.get<SubmitAdvice>('/api/tools/submit-advisor'),
}
