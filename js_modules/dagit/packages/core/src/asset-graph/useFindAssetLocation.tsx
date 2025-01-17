import {useApolloClient} from '@apollo/client';
import React from 'react';

import {AssetKey} from '../assets/types';
import {graphql} from '../graphql';
import {
  AssetForNavigationQueryQuery,
  AssetForNavigationQueryQueryVariables,
  AssetKeyInput,
} from '../graphql/graphql';
import {buildRepoAddress} from '../workspace/buildRepoAddress';
import {RepoAddress} from '../workspace/types';

import {isHiddenAssetGroupJob} from './Utils';

export interface AssetLocation {
  assetKey: AssetKey;
  opNames: string[];
  jobName: string | null;
  groupName: string | null;
  repoAddress: RepoAddress | null;
}

export function useFindAssetLocation() {
  const apollo = useApolloClient();

  return React.useCallback(
    async (key: AssetKeyInput): Promise<AssetLocation> => {
      const {data} = await apollo.query<
        AssetForNavigationQueryQuery,
        AssetForNavigationQueryQueryVariables
      >({
        query: ASSET_FOR_NAVIGATION_QUERY,
        variables: {key},
      });
      if (data?.assetOrError.__typename === 'Asset' && data?.assetOrError.definition) {
        const def = data.assetOrError.definition;
        return {
          assetKey: key,
          opNames: def.opNames,
          jobName: def.jobNames.find((jobName) => !isHiddenAssetGroupJob(jobName)) || null,
          groupName: def.groupName,
          repoAddress: def.repository
            ? buildRepoAddress(def.repository.name, def.repository.location.name)
            : null,
        };
      }
      return {assetKey: key, opNames: [], jobName: null, groupName: null, repoAddress: null};
    },
    [apollo],
  );
}

const ASSET_FOR_NAVIGATION_QUERY = graphql(`
  query AssetForNavigationQuery($key: AssetKeyInput!) {
    assetOrError(assetKey: $key) {
      __typename
      ... on Asset {
        id
        definition {
          id
          opNames
          jobNames
          groupName
          repository {
            id
            name
            location {
              id
              name
            }
          }
        }
      }
    }
  }
`);
