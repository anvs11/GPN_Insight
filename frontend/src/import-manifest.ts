// Здесь только ограничения текущего synthetic text-only экрана.
// Строгую проверку JSON, дат, ID, повторов и counts выполняет Backend.
export const maxManifestBytes = 1_048_576

export interface ImportTicketDraft {
  source_id: string
  created_at: string
  text: string
  requester_id: string
}

export function textManifest(datasetId: string, ticket: ImportTicketDraft): string {
  return JSON.stringify({ dataset_id: datasetId, synthetic: true, tickets: [{
    source_id: ticket.source_id, product_id: 'webneft', created_at: ticket.created_at,
    text: ticket.text, requester_id: ticket.requester_id || null, assets: [],
  }] })
}

export function readTextManifest(raw: string): { datasetId: string; tickets: { source_id: string; text: string; created_at: string }[] } {
  if (new TextEncoder().encode(raw).byteLength > maxManifestBytes) throw new Error('JSON превышает 1 МиБ. Разделите набор.')
  let value: unknown
  try { value = JSON.parse(raw) } catch { throw new Error('JSON не разобран. Выберите корректный UTF-8 JSON.') }
  if (typeof value !== 'object' || value === null || !('synthetic' in value) || value.synthetic !== true) {
    throw new Error('Этот экран принимает только явно синтетические наборы с synthetic=true. Реальные данные пока не согласованы.')
  }
  if (!('dataset_id' in value) || typeof value.dataset_id !== 'string' || !('tickets' in value) ||
    !Array.isArray(value.tickets) || value.tickets.length > 100) throw new Error('Нужны dataset_id и tickets: массив не более 100 обращений.')
  const tickets = value.tickets.map((item: unknown) => {
    if (typeof item !== 'object' || item === null || !('source_id' in item) || typeof item.source_id !== 'string' ||
      !('text' in item) || typeof item.text !== 'string' || !item.text.trim() ||
      !('created_at' in item) || typeof item.created_at !== 'string') throw new Error('Каждому текстовому обращению нужны source_id, created_at и непустой text.')
    if ('assets' in item && (!Array.isArray(item.assets) || item.assets.length !== 0)) {
      throw new Error('Здесь доступен только текстовый импорт без вложений. Медиа импортируются отдельным серверным сценарием.')
    }
    if ('product_id' in item && item.product_id !== 'webneft') throw new Error('В пилоте доступен только продукт webneft.')
    return { source_id: item.source_id, created_at: item.created_at, text: item.text }
  })
  // Исходный raw отправляется без пересборки: повторные ключи и лишние поля
  // должны быть отклонены сервером, а не незаметно исправлены браузером.
  return { datasetId: value.dataset_id, tickets }
}
