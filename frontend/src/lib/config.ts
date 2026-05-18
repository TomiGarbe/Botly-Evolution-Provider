const STORAGE_URL = 'botly_gateway_url'
const STORAGE_KEY = 'botly_gateway_key'
const STORAGE_PUBLIC_BASE_URL = 'botly_public_base_url'

export interface GatewayConfig {
  url: string
  apiKey: string
  publicBaseUrl: string
}

export function loadConfig(): GatewayConfig {
  const fallbackUrl = import.meta.env.VITE_GATEWAY_URL ?? 'http://localhost:9000'
  return {
    url: localStorage.getItem(STORAGE_URL) ?? fallbackUrl,
    apiKey: localStorage.getItem(STORAGE_KEY) ?? import.meta.env.VITE_GATEWAY_API_KEY ?? '',
    publicBaseUrl: localStorage.getItem(STORAGE_PUBLIC_BASE_URL) ?? import.meta.env.VITE_PUBLIC_BASE_URL ?? fallbackUrl,
  }
}

export function saveConfig(cfg: GatewayConfig): void {
  localStorage.setItem(STORAGE_URL, cfg.url)
  localStorage.setItem(STORAGE_KEY, cfg.apiKey)
  localStorage.setItem(STORAGE_PUBLIC_BASE_URL, cfg.publicBaseUrl)
}
