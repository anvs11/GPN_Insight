import { maxManifestBytes } from './import-manifest'

const mimes = new Set(['audio/wav', 'audio/mpeg', 'image/png', 'image/jpeg'])
export function readMediaManifest(raw: string, files: readonly File[]) {
  if (new TextEncoder().encode(raw).length > maxManifestBytes) throw new Error('JSON превышает 1 МиБ.')
  const value: unknown = JSON.parse(raw)
  if (typeof value !== 'object' || value === null || !('synthetic' in value) || value.synthetic !== true ||
    !('dataset_id' in value) || typeof value.dataset_id !== 'string' || !('tickets' in value) ||
    !Array.isArray(value.tickets) || value.tickets.length === 0 || value.tickets.length > 100) {
    throw new Error('Нужен synthetic=true, dataset_id и от 1 до 100 обращений tickets.')
  }
  const expected = new Map<string, string>()
  const sourceIds: string[] = []
  for (const ticket of value.tickets) {
    if (typeof ticket !== 'object' || ticket === null || typeof ticket.source_id !== 'string' || typeof ticket.created_at !== 'string' ||
      (ticket.text !== undefined && typeof ticket.text !== 'string') || !Array.isArray(ticket.assets) || (ticket.product_id !== undefined && ticket.product_id !== 'webneft') ||
      (!(ticket.text ?? '').trim() && !ticket.assets.length)) throw new Error('Нужны source_id, created_at и assets. Пустой или отсутствующий текст допустим при наличии вложения.')
    sourceIds.push(ticket.source_id)
    for (const asset of ticket.assets) {
      if (typeof asset !== 'object' || asset === null || typeof asset.path !== 'string' || typeof asset.asset_id !== 'string' ||
        !mimes.has(asset.mime_type) || !['audio', 'image'].includes(asset.kind)) throw new Error('Вложения: WAV/MP3 или PNG/JPEG с asset_id, path, kind и mime_type.')
      if (asset.path !== asset.path.normalize('NFC') || /[\\/:]/.test(asset.path) || [...asset.path].some(c => c.charCodeAt(0) < 32) || !asset.path || asset.path === '.' || asset.path === '..') {
        throw new Error('В path допустимо только простое имя файла без каталогов и ссылок.')
      }
      if (expected.has(asset.path) && expected.get(asset.path) !== asset.mime_type) throw new Error('Один файл указан с противоречивым MIME.')
      expected.set(asset.path, asset.mime_type)
    }
  }
  if (files.length > 30 || files.reduce((n, file) => n + file.size, 0) > 50 * 1048576) throw new Error('Не более 30 файлов суммарно до 50 МиБ.')
  const names = new Set<string>()
  for (const file of files) {
    if (names.has(file.name.toLowerCase())) throw new Error('Имена выбранных файлов повторяются. Используйте однозначные имена.')
    names.add(file.name.toLowerCase())
    const mime = expected.get(file.name)
    if (!mime) throw new Error('Выбран лишний файл: набор файлов должен точно совпасть с assets.path.')
    if (file.size === 0 || file.size > (mime.startsWith('audio/') ? 10 : 5) * 1048576) throw new Error('Пустой или слишком большой файл: аудио до 10 МиБ, изображения до 5 МиБ.')
  }
  if (files.length !== expected.size || [...expected.keys()].some(name => !files.some(file => file.name === name))) {
    throw new Error('Выберите все файлы, перечисленные в assets.path, с точными именами.')
  }
  return { datasetId: value.dataset_id, sourceIds }
}
