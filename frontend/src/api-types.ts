// Ответы /api/v1/health и /runs: backend/app/schemas.py и /openapi.json.
// Сервер сериализует также поля с default и null; режим клиент не задаёт.
export type ModelMode = 'live' | 'replay'
export type Origin = 'model' | 'authored'

export interface ModelAvailability {
  status: 'available' | 'unavailable' | 'not_checked' | 'not_configured' | 'not_implemented'
  configured: boolean
  checked_at: string | null
  code: string
  message: string
}

export interface Health {
  status: 'ok'
  model_mode: ModelMode
  processing_state: 'idle' | 'busy' | 'not_implemented'
  available_models: Partial<Record<'text' | 'asr' | 'vision', ModelAvailability>>
  replay_origins: Origin[]
  replay_record_count: number
  pipeline_ready: boolean
  integration_errors: string[]
}

export interface ApiErrorBody {
  code: string
  message: string
  run_id?: string | null
}

export interface RunRequest {
  product_id: 'webneft'
  date_from?: string | null
  date_to?: string | null
}

export interface MaterialError {
  source_id: string
  asset_id: string | null
  stage: string
  code: string
  message: string
}

export interface Run {
  run_id: string
  product_id: string
  mode: ModelMode
  state: 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'interrupted'
  stage: string
  successful_materials: number
  failed_materials: number
  total_materials: number
  coverage: number | null
  errors: MaterialError[]
  created_at: string
  started_at: string | null
  finished_at: string | null
  origins: Origin[]
  completed_steps: number
  reused_steps: number
  failure_code: string | null
  failure_message: string | null
}
