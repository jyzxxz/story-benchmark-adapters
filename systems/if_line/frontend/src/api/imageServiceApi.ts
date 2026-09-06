import api from './index'

export interface ImageServiceStatus {
  enabled: boolean
  model: string
}

export const imageServiceApi = {
  status() {
    return api.get<ImageServiceStatus>('/image-generation/status')
  },
}
