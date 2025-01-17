from typing import AbstractSet, Mapping, NamedTuple, Optional, Sequence, Set, Union
from unittest.mock import MagicMock, patch

import pendulum
import pytest
from dagster_tests.definitions_tests.test_asset_reconciliation_sensor import (
    RunSpec,
    do_run,
    non_partitioned_after_partitioned,
    one_asset_one_partition,
    one_asset_self_dependency,
    one_asset_two_partitions,
    partitioned_after_non_partitioned,
    two_assets_in_sequence_fan_in_partitions,
    two_assets_in_sequence_fan_out_partitions,
    two_assets_in_sequence_one_partition,
    two_assets_in_sequence_two_partitions,
)

from dagster import (
    AssetSelection,
    AssetsDefinition,
    DagsterInstance,
    Definitions,
    RunRequest,
    SourceAsset,
)
from dagster._core.definitions.asset_graph_subset import AssetGraphSubset
from dagster._core.definitions.events import AssetKeyPartitionKey
from dagster._core.definitions.external_asset_graph import ExternalAssetGraph
from dagster._core.execution.asset_backfill import (
    AssetBackfillData,
    AssetBackfillIterationResult,
    execute_asset_backfill_iteration_inner,
)
from dagster._core.host_representation.external_data import external_asset_graph_from_defs
from dagster._seven.compat.pendulum import create_pendulum_time


class AssetBackfillScenario(NamedTuple):
    assets: Sequence[Union[SourceAsset, AssetsDefinition]]
    unevaluated_runs: Sequence[RunSpec] = []
    expected_run_requests: Optional[Sequence[RunRequest]] = None
    expected_iterations: Optional[int] = None


assets_by_repo_name_by_scenario_name: Mapping[str, Mapping[str, Sequence[AssetsDefinition]]] = {
    "one_asset_one_partition": {"repo": one_asset_one_partition},
    "one_asset_two_partitions": {"repo": one_asset_two_partitions},
    "two_assets_in_sequence_one_partition": {"repo": two_assets_in_sequence_one_partition},
    "two_assets_in_sequence_one_partition_cross_repo": {
        "repo1": [two_assets_in_sequence_one_partition[0]],
        "repo2": [two_assets_in_sequence_one_partition[1]],
    },
    "two_assets_in_sequence_two_partitions": {"repo": two_assets_in_sequence_two_partitions},
    "two_assets_in_sequence_fan_in_partitions": {"repo": two_assets_in_sequence_fan_in_partitions},
    "two_assets_in_sequence_fan_out_partitions": {
        "repo": two_assets_in_sequence_fan_out_partitions
    },
    "one_asset_self_dependency": {"repo": one_asset_self_dependency},
    "non_partitioned_after_partitioned": {"repo": non_partitioned_after_partitioned},
    "partitioned_after_non_partitioned": {"repo": partitioned_after_non_partitioned},
}


@pytest.mark.parametrize("some_or_all", ["all", "some"])
@pytest.mark.parametrize("failures", ["no_failures", "root_failures", "random_half_failures"])
@pytest.mark.parametrize("scenario_name", list(assets_by_repo_name_by_scenario_name.keys()))
def test_scenario_to_completion(scenario_name: str, failures: str, some_or_all: str):
    with pendulum.test(create_pendulum_time(year=2020, month=1, day=7, hour=4)):
        assets_by_repo_name = assets_by_repo_name_by_scenario_name[scenario_name]

        with patch(
            "dagster._core.host_representation.external_data.get_builtin_partition_mapping_types"
        ) as get_builtin_partition_mapping_types:
            get_builtin_partition_mapping_types.return_value = tuple(
                assets_def.infer_partition_mapping(dep_key).__class__
                for assets in assets_by_repo_name.values()
                for assets_def in assets
                for dep_key in assets_def.dependency_keys
            )
            asset_graph = external_asset_graph_from_assets_by_repo_name(assets_by_repo_name)

        asset_keys = asset_graph.all_asset_keys
        root_asset_keys = AssetSelection.keys(*asset_keys).sources().resolve(asset_graph)

        if some_or_all == "all":
            target_subset = AssetGraphSubset.all(asset_graph)
        elif some_or_all == "some":
            # all partitions downstream of half of the partitions in each partitioned root asset
            root_asset_partitions: Set[AssetKeyPartitionKey] = set()
            for i, root_asset_key in enumerate(sorted(root_asset_keys)):
                partitions_def = asset_graph.get_partitions_def(root_asset_key)

                if partitions_def is not None:
                    partition_keys = list(partitions_def.get_partition_keys())
                    start_index = len(partition_keys) // 2
                    chosen_partition_keys = partition_keys[start_index:]
                    root_asset_partitions.update(
                        AssetKeyPartitionKey(root_asset_key, partition_key)
                        for partition_key in chosen_partition_keys
                    )
                else:
                    if i % 2 == 0:
                        root_asset_partitions.add(AssetKeyPartitionKey(root_asset_key, None))

            target_asset_partitions = asset_graph.bfs_filter_asset_partitions(
                lambda _a, _b: True, root_asset_partitions
            )

            target_subset = AssetGraphSubset.from_asset_partition_set(
                target_asset_partitions, asset_graph
            )

        else:
            assert False

        if failures == "no_failures":
            fail_asset_partitions: Set[AssetKeyPartitionKey] = set()
        elif failures == "root_failures":
            fail_asset_partitions = set(
                (target_subset.filter_asset_keys(root_asset_keys)).iterate_asset_partitions()
            )
        elif failures == "random_half_failures":
            fail_asset_partitions = {
                asset_partition
                for asset_partition in target_subset.iterate_asset_partitions()
                if hash(str(asset_partition.asset_key) + str(asset_partition.partition_key)) % 2
                == 0
            }

        else:
            assert False

        backfill = AssetBackfillData.empty(target_subset)
        run_backfill_to_completion(
            asset_graph, assets_by_repo_name, "backfillid_x", backfill, fail_asset_partitions
        )


def execute_asset_backfill_iteration_consume_generator(
    backfill_id: str,
    asset_backfill_data: AssetBackfillData,
    asset_graph: ExternalAssetGraph,
    instance: DagsterInstance,
) -> AssetBackfillIterationResult:
    for result in execute_asset_backfill_iteration_inner(
        backfill_id=backfill_id,
        asset_backfill_data=asset_backfill_data,
        instance=instance,
        asset_graph=asset_graph,
    ):
        if isinstance(result, AssetBackfillIterationResult):
            return result

    assert False


def run_backfill_to_completion(
    asset_graph: ExternalAssetGraph,
    assets_by_repo_name: Mapping[str, Sequence[AssetsDefinition]],
    backfill_id: str,
    backfill_data: AssetBackfillData,
    fail_asset_partitions: AbstractSet[AssetKeyPartitionKey],
) -> None:
    iteration_count = 0
    instance = DagsterInstance.ephemeral()

    # assert each asset partition only targeted once
    requested_asset_partitions: Set[AssetKeyPartitionKey] = set()

    fail_and_downstream_asset_partitions = asset_graph.bfs_filter_asset_partitions(
        lambda _a, _b: True, fail_asset_partitions
    )

    while not backfill_data.is_complete():
        iteration_count += 1
        result1 = execute_asset_backfill_iteration_consume_generator(
            backfill_id=backfill_id,
            asset_backfill_data=backfill_data,
            asset_graph=asset_graph,
            instance=instance,
        )
        # iteration_count += 1
        assert result1.backfill_data != backfill_data

        # if nothing changes, nothing should happen in the iteration
        result2 = execute_asset_backfill_iteration_consume_generator(
            backfill_id=backfill_id,
            asset_backfill_data=result1.backfill_data,
            asset_graph=asset_graph,
            instance=instance,
        )
        assert result2.backfill_data == result1.backfill_data
        assert result2.run_requests == []

        backfill_data = result2.backfill_data

        for asset_partition in backfill_data.materialized_subset.iterate_asset_partitions():
            assert asset_partition not in fail_and_downstream_asset_partitions

            for parent_asset_partition in asset_graph.get_parents_partitions(*asset_partition):
                if (
                    parent_asset_partition in backfill_data.target_subset
                    and parent_asset_partition not in backfill_data.materialized_subset
                ):
                    assert (
                        False
                    ), f"{asset_partition} was materialized before its parent {parent_asset_partition},"

        for run_request in result1.run_requests:
            asset_keys = run_request.asset_selection
            assert asset_keys is not None

            for asset_key in asset_keys:
                asset_partition = AssetKeyPartitionKey(asset_key, run_request.partition_key)
                assert (
                    asset_partition not in requested_asset_partitions
                ), f"{asset_partition} requested twice. Requested: {requested_asset_partitions}."
                requested_asset_partitions.add(asset_partition)

            assert all(
                asset_graph.get_repository_handle(asset_keys[0])
                == asset_graph.get_repository_handle(asset_key)
                for asset_key in asset_keys
            )
            assets = assets_by_repo_name[
                asset_graph.get_repository_handle(asset_keys[0]).repository_name
            ]

            do_run(
                all_assets=assets,
                asset_keys=asset_keys,
                partition_key=run_request.partition_key,
                instance=instance,
                failed_asset_keys=[
                    asset_key
                    for asset_key in asset_keys
                    if AssetKeyPartitionKey(asset_key, run_request.partition_key)
                    in fail_asset_partitions
                ],
                tags=run_request.tags,
            )

        assert iteration_count <= len(requested_asset_partitions) + 1

    assert (
        len(requested_asset_partitions | fail_and_downstream_asset_partitions)
        == backfill_data.target_subset.num_partitions_and_non_partitioned_assets
    )


def external_asset_graph_from_assets_by_repo_name(
    assets_by_repo_name: Mapping[str, Sequence[AssetsDefinition]],
) -> ExternalAssetGraph:
    from_repository_handles_and_external_asset_nodes = []

    for repo_name, assets in assets_by_repo_name.items():

        repo = Definitions(assets=assets).get_repository_def()

        external_asset_nodes = external_asset_graph_from_defs(
            repo.get_all_pipelines(), source_assets_by_key=repo.source_assets_by_key
        )
        repo_handle = MagicMock(repository_name=repo_name)
        from_repository_handles_and_external_asset_nodes.extend(
            [(repo_handle, asset_node) for asset_node in external_asset_nodes]
        )

    return ExternalAssetGraph.from_repository_handles_and_external_asset_nodes(
        from_repository_handles_and_external_asset_nodes
    )
