import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import {
  createLibraryAssetApi,
  type LibraryAssetHttpClient,
  type LibraryFacetResponse,
  type VnLibraryAssetPage,
} from '../libraryAssetApi'

interface RecordedCall {
  url: string
  config?: AxiosRequestConfig
}

function response<T>(data: T, status = 200): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    headers: {},
    config: { headers: {} } as AxiosResponse<T>['config'],
  }
}

function axiosError(status?: number) {
  return {
    name: 'AxiosError',
    message: status ? `HTTP ${status}` : 'Network Error',
    isAxiosError: true,
    response: status ? { status } : undefined,
  }
}

function facets(
  catalogVersion: string,
  assetType: 'background' | 'portrait' | 'keyframe',
): LibraryFacetResponse {
  return {
    catalog_version: catalogVersion,
    asset_type: assetType,
    selected_tag_ids: [],
    total: 2,
    facets: [
      {
        category: 'style',
        options: [
          {
            id: `${assetType}-historical-id`,
            value: 'historical',
            display_name: 'Historical',
            count: 2,
            selected: false,
          },
        ],
      },
    ],
  }
}

function page(): VnLibraryAssetPage {
  return { items: [], total: 0 }
}

test('search uses object parameters and serializes unique tag IDs in input order', async () => {
  const calls: RecordedCall[] = []
  const client: LibraryAssetHttpClient = {
    get<T>(url: string, config?: AxiosRequestConfig) {
      calls.push({ url, config })
      return Promise.resolve(response(page()) as AxiosResponse<T>)
    },
  }
  const library = createLibraryAssetApi(client)

  await library.listLibraryAssets({
    assetType: 'background',
    tagIds: [' tag-b ', 'tag-a', 'tag-b', ''],
    query: 'rainy street',
    limit: 20,
    offset: 10,
  })

  assert.equal(calls[0].url, '/library-assets')
  assert.deepEqual(calls[0].config?.params, {
    asset_type: 'background',
    tag_ids: 'tag-b,tag-a',
    query: 'rainy street',
    limit: 20,
    offset: 10,
    style: undefined,
  })
})
test('base Facet cache records catalog version and coalesces same-type requests', async () => {
  const calls: RecordedCall[] = []
  let complete!: (value: AxiosResponse<LibraryFacetResponse>) => void
  const pending = new Promise<AxiosResponse<LibraryFacetResponse>>((resolve) => {
    complete = resolve
  })
  const client: LibraryAssetHttpClient = {
    get<T>(url: string, config?: AxiosRequestConfig) {
      calls.push({ url, config })
      return pending as Promise<AxiosResponse<T>>
    },
  }
  const library = createLibraryAssetApi(client)

  const first = library.resolveLibraryTagId('background', ' Style ', ' HISTORICAL ')
  const second = library.resolveLibraryTagId('background', 'style', 'historical')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/library-assets/facets')
  complete(response(facets('catalog-v7', 'background')))

  assert.equal(await first, 'background-historical-id')
  assert.equal(await second, 'background-historical-id')
  assert.equal(
    await library.resolveLibraryTagId('background', 'style', 'historical'),
    'background-historical-id',
  )
  assert.equal(calls.length, 1)
})

test('resolved Facet IDs are used for search', async () => {
  const calls: RecordedCall[] = []
  const client: LibraryAssetHttpClient = {
    get<T>(url: string, config?: AxiosRequestConfig) {
      calls.push({ url, config })
      if (url.endsWith('/facets')) {
        return Promise.resolve(
          response(facets('catalog-v1', 'portrait')) as AxiosResponse<T>,
        )
      }
      return Promise.resolve(response(page()) as AxiosResponse<T>)
    },
  }
  const library = createLibraryAssetApi(client)

  await library.listByFacetValue({
    assetType: 'portrait',
    category: 'style',
    value: 'historical',
    limit: 100,
  })

  assert.equal(calls.length, 2)
  assert.equal(calls[1].url, '/library-assets')
  assert.equal(calls[1].config?.params.tag_ids, 'portrait-historical-id')
  assert.equal(calls[1].config?.params.style, undefined)
})

test('only a Facet 404 falls back to the legacy style parameter', async () => {
  const calls: RecordedCall[] = []
  const client: LibraryAssetHttpClient = {
    get<T>(url: string, config?: AxiosRequestConfig) {
      calls.push({ url, config })
      if (url.endsWith('/facets')) return Promise.reject(axiosError(404))
      return Promise.resolve(response(page()) as AxiosResponse<T>)
    },
  }
  const library = createLibraryAssetApi(client)

  await library.listByFacetValue({
    assetType: 'background',
    category: 'style',
    value: 'historical',
  })

  assert.equal(calls.length, 2)
  assert.equal(calls[1].config?.params.style, 'historical')
  assert.equal(calls[1].config?.params.tag_ids, undefined)
})

for (const status of [401, 422, 500, undefined]) {
  test(`Facet ${status ?? 'network'} errors never trigger legacy fallback`, async () => {
    const calls: RecordedCall[] = []
    const client: LibraryAssetHttpClient = {
      get<T>(url: string, config?: AxiosRequestConfig) {
        calls.push({ url, config })
        return Promise.reject(axiosError(status)) as Promise<AxiosResponse<T>>
      },
    }
    const library = createLibraryAssetApi(client)

    await assert.rejects(
      library.listByFacetValue({
        assetType: 'background',
        category: 'style',
        value: 'historical',
      }),
    )
    assert.equal(calls.length, 1)
  })
}

test('missing options and non-style 404s never degrade to an unfiltered search', async () => {
  let missingCalls = 0
  const missingClient: LibraryAssetHttpClient = {
    get<T>() {
      missingCalls += 1
      return Promise.resolve(response(facets('catalog-v1', 'background')) as AxiosResponse<T>)
    },
  }
  const missingLibrary = createLibraryAssetApi(missingClient)
  await assert.rejects(
    missingLibrary.listByFacetValue({
      assetType: 'background',
      category: 'style',
      value: 'xianxia',
    }),
    /Library facet style=xianxia is missing/,
  )
  assert.equal(missingCalls, 1)

  let notFoundCalls = 0
  const notFoundClient: LibraryAssetHttpClient = {
    get<T>() {
      notFoundCalls += 1
      return Promise.reject(axiosError(404)) as Promise<AxiosResponse<T>>
    },
  }
  const notFoundLibrary = createLibraryAssetApi(notFoundClient)
  await assert.rejects(
    notFoundLibrary.listByFacetValue({
      assetType: 'background',
      category: 'location',
      value: 'street',
    }),
  )
  assert.equal(notFoundCalls, 1)
})
