import 'axios'

declare module 'axios' {
  export interface AxiosRequestConfig {
    silent401?: boolean
  }
}
