import axios, { type AxiosRequestConfig, type AxiosResponse } from 'axios'
import api from './index'

export type LibraryAssetType = 'portrait' | 'background' | 'keyframe'

export interface VnPortraitPresentation {
  canvas_width: number
  canvas_height: number
  left_ratio?: number
  right_ratio?: number
  top_ratio?: number
  bottom_ratio?: number
  limiting_axis?: string
  scale_factor?: number
}

export interface VnLibraryAsset {
  id: string
  stable_key: string
  asset_type: LibraryAssetType
  media_url: string
  presentation_url?: string | null
  presentation?: VnPortraitPresentation | null
  tags: string[]
  style: string | null
  identity_group: string | null
}

export interface VnLibraryAssetPage {
  items: VnLibraryAsset[]
  total: number
  catalog_version?: string
  matcher_version?: string
  limit?: number
  offset?: number
}

export interface LibraryFacetOption {
  id: string
  value: string
  display_name: string
  count: number
  selected: boolean
}

export interface LibraryFacetGroup {
  category: string
  options: LibraryFacetOption[]
}

export interface LibraryFacetResponse {
  catalog_version: string
  asset_type: LibraryAssetType
  selected_tag_ids: string[]
  total: number
  facets: LibraryFacetGroup[]
}

export interface ListLibraryAssetsParams {
  assetType?: LibraryAssetType
  tagIds?: string[]
  query?: string
  limit?: number
  offset?: number
  legacyStyle?: string
}

export interface GetLibraryFacetsParams {
  assetType: LibraryAssetType
  tagIds?: string[]
}

export interface ListByFacetValueParams {
  assetType: LibraryAssetType
  category: string
  value: string
  query?: string
  limit?: number
  offset?: number
}

export interface LibraryAssetHttpClient {
  get<T>(url: string, config?: AxiosRequestConfig): Promise<AxiosResponse<T>>
}

function serializeTagIds(tagIds?: string[]): string | undefined {
  const unique = [...new Set((tagIds ?? []).map((item) => item.trim()).filter(Boolean))]
  return unique.length ? unique.join(',') : undefined
}

function normalizeCategory(value: string): string {
  return value
    .normalize('NFKC')
    .trim()
    .replace(/[-\s]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase()
}

function normalizeValue(value: string): string {
  return value.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase()
}

export function createLibraryAssetApi(client: LibraryAssetHttpClient = api) {
  const facetCache = new Map<string, LibraryFacetResponse>()
  const currentFacetKey = new Map<LibraryAssetType, string>()
  const pendingFacets = new Map<LibraryAssetType, Promise<LibraryFacetResponse>>()

  const listLibraryAssets = (params: ListLibraryAssetsParams) =>
    client.get<VnLibraryAssetPage>('/library-assets', {
      params: {
        asset_type: params.assetType,
        tag_ids: serializeTagIds(params.tagIds),
        query: params.query,
        limit: params.limit,
        offset: params.offset,
        style: params.legacyStyle,
      },
    })

  const getLibraryFacets = (params: GetLibraryFacetsParams) =>
    client.get<LibraryFacetResponse>('/library-assets/facets', {
      params: {
        asset_type: params.assetType,
        tag_ids: serializeTagIds(params.tagIds),
      },
    })

  const loadBaseFacets = async (assetType: LibraryAssetType): Promise<LibraryFacetResponse> => {
    const existingKey = currentFacetKey.get(assetType)
    if (existingKey) {
      const cached = facetCache.get(existingKey)
      if (cached) return cached
    }
    const pending = pendingFacets.get(assetType)
    if (pending) return pending

    const request = getLibraryFacets({ assetType })
      .then(({ data }) => {
        const key = `${data.catalog_version}:${assetType}`
        facetCache.set(key, data)
        currentFacetKey.set(assetType, key)
        return data
      })
      .finally(() => pendingFacets.delete(assetType))
    pendingFacets.set(assetType, request)
    return request
  }

  const resolveLibraryTagId = async (
    assetType: LibraryAssetType,
    category: string,
    value: string,
  ): Promise<string> => {
    const facets = await loadBaseFacets(assetType)
    const normalizedCategory = normalizeCategory(category)
    const normalizedValue = normalizeValue(value)
    const group = facets.facets.find((item) => item.category === normalizedCategory)
    const option = group?.options.find((item) => item.value === normalizedValue)
    if (!option) {
      throw new Error(
        `Library facet ${normalizedCategory}=${normalizedValue} is missing for ${assetType}`,
      )
    }
    return option.id
  }

  const listByFacetValue = async (
    params: ListByFacetValueParams,
  ): Promise<AxiosResponse<VnLibraryAssetPage>> => {
    let tagId: string
    try {
      tagId = await resolveLibraryTagId(params.assetType, params.category, params.value)
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 404) {
        if (normalizeCategory(params.category) !== 'style') throw error
        return listLibraryAssets({
          assetType: params.assetType,
          query: params.query,
          limit: params.limit,
          offset: params.offset,
          legacyStyle: params.value,
        })
      }
      throw error
    }
    return listLibraryAssets({
      assetType: params.assetType,
      tagIds: [tagId],
      query: params.query,
      limit: params.limit,
      offset: params.offset,
    })
  }

  const clearFacetCache = () => {
    facetCache.clear()
    currentFacetKey.clear()
    pendingFacets.clear()
  }

  return {
    listLibraryAssets,
    getLibraryFacets,
    resolveLibraryTagId,
    listByFacetValue,
    clearFacetCache,
  }
}

export const libraryAssetApi = createLibraryAssetApi()
