import os
from mlflow.exceptions import RestException
from mlflow_export_import.bulk.export_models import export_models
from mlflow_export_import.bulk.import_models import import_all
from mlflow_export_import.bulk import bulk_utils

from init_tests import mlflow_context
from compare_utils import compare_runs
from test_bulk_experiments import create_test_experiment
from oss_utils_test import (
    mk_test_object_name_default,
    mk_uuid,
    list_experiments,
    delete_experiments_and_models
)

# == Setup

_notebook_formats = "SOURCE,DBC"
_num_models = 2
_num_runs = 2


# == Compare

def compare_models_with_versions(mlflow_context, compare_func):
    test_dir = os.path.join(mlflow_context.output_dir, "test_compare_runs")
    exps = list_experiments(mlflow_context.client_src)
    exp_ids = [ exp.experiment_id for exp in exps ]
    models2 = mlflow_context.client_dst.search_registered_models()
    assert len(models2) > 0
    for model2 in models2:
        versions2 = mlflow_context.client_dst.get_latest_versions(model2.name)
        for vr in versions2:
            run2 = mlflow_context.client_dst.get_run(vr.run_id)
            tag = run2.data.tags["my_uuid"]
            filter = f"tags.my_uuid = '{tag}'"
            run1 = mlflow_context.client_src.search_runs(exp_ids, filter)[0]
            tdir = os.path.join(test_dir,run2.info.run_id)
            os.makedirs(tdir)
            assert run1.info.run_id != run2.info.run_id
            compare_func(mlflow_context.client_src, mlflow_context.client_dst, run1, run2, tdir)


# == Helper methods

def create_model(client):
    model_name, _ = _create_model(client)
    return model_name

def _create_model(client):
    exp = create_test_experiment(client, _num_runs)
    model_name = mk_test_object_name_default()
    model = client.create_registered_model(model_name)
    for run in client.search_runs([exp.experiment_id]):
        source = f"{run.info.artifact_uri}/model"
        client.create_model_version(model_name, source, run.info.run_id)
    return model.name, exp

def _run_test(mlflow_context, compare_func, use_threads=False):
    delete_experiments_and_models(mlflow_context)
    model_names = [ create_model(mlflow_context.client_src) for j in range(0, _num_models) ]
    export_models(
        mlflow_client = mlflow_context.client_src,
        model_names = model_names, 
        output_dir = mlflow_context.output_dir, 
        notebook_formats = _notebook_formats, 
        stages = "None", 
        export_all_runs = False, 
        use_threads = False
    )

    import_all(
        mlflow_client = mlflow_context.client_dst,
        input_dir = mlflow_context.output_dir,
        delete_model = False,
        use_src_user_id = False,
        verbose = False,
        use_threads = use_threads
    )

    compare_models_with_versions(mlflow_context, compare_func)


# == Export/import registered model tests

def test_basic(mlflow_context):
    _run_test(mlflow_context, compare_runs)

def test_exp_basic_threads(mlflow_context):
    _run_test(mlflow_context, compare_runs, use_threads=True)

def test_exp_with_source_tags(mlflow_context):
    _run_test(mlflow_context, compare_runs)

# == Test number if all runs of an experiment or just those of the version are exported

def _add_version(client, model_name, run, stage):
    source = f"{run.info.artifact_uri}/model"
    vr = client.create_model_version(model_name, source, run.info.run_id)
    client.transition_model_version_stage(model_name, vr.version, stage)

def _export_models(client, model_name, output_dir, export_all_runs):
    export_models(
        mlflow_client = client,
        model_names = [ model_name ],
        output_dir = output_dir, 
        stages = "production,staging",
        export_all_runs = export_all_runs
    )

_num_runs1, _num_runs2 = (2, 3)

def _run_test_export_runs(mlflow_context, export_all_runs):
    delete_experiments_and_models(mlflow_context)
    client1 = mlflow_context.client_src
    exp1 = create_test_experiment(client1, _num_runs1)
    exp2 = create_test_experiment(client1, _num_runs2)
    model_name = mk_test_object_name_default()
    client1.create_registered_model(model_name)

    runs1 = client1.search_runs([exp1.experiment_id])
    _add_version(client1, model_name, runs1[0], "production")
    runs2 = client1.search_runs([exp2.experiment_id])
    _add_version(client1, model_name, runs2[0], "staging")
    client1.get_latest_versions(model_name)

    _export_models(client1, model_name, mlflow_context.output_dir, export_all_runs)
    
    client2 = mlflow_context.client_dst
    import_all(
        mlflow_client = client2, 
        input_dir = mlflow_context.output_dir, 
        delete_model = False
    )
    exps2 = client2.search_experiments()
    runs2 = client2.search_runs([exp.experiment_id for exp in exps2])
    return len(runs2)


def test_export_all_experiment_runs(mlflow_context):
    """ 
    Test that we export all runs of experiments that are referenced by version runs.
    """
    num_runs = _run_test_export_runs(mlflow_context, True)
    assert num_runs == _num_runs1 + _num_runs2


def test_export_only_version_runs(mlflow_context):
    """ 
    Test that we export only runs referenced by version.
    """
    num_runs = _run_test_export_runs(mlflow_context, False)
    assert num_runs == 2


# == Parsing tests for extracting model names from CLI comma-delimitd string option 

def test_get_model_names_from_comma_delimited_string(mlflow_context):
    delete_experiments_and_models(mlflow_context)
    model_names = bulk_utils.get_model_names(mlflow_context.client_src,"model1,model2,model3")
    assert len(model_names) == 3


def test_get_model_names_from_all_string(mlflow_context):
    delete_experiments_and_models(mlflow_context)
    model_names1 = [ create_model(mlflow_context.client_src) for j in range(0,3) ]
    model_names2 = bulk_utils.get_model_names(mlflow_context.client_src, "*")
    assert set(model_names1) == set(model_names2)


# == Test import with experiment replacement tests

def test_replace_experiment_names_do_replace(mlflow_context):
    model_name, exp = _create_model(mlflow_context.client_src)
    export_models(
        model_names = [ model_name ],
        mlflow_client = mlflow_context.client_src,
        output_dir = mlflow_context.output_dir, 
    )
    new_exp_name = mk_uuid()
    import_all(
        mlflow_client = mlflow_context.client_dst,
        input_dir = mlflow_context.output_dir,
        delete_model = True,
        experiment_name_replacements = { exp.name: new_exp_name }
    )

    exp2 = mlflow_context.client_dst.get_experiment_by_name(exp.name)
    assert not exp2
    exp2 = mlflow_context.client_dst.get_experiment_by_name(new_exp_name)
    assert exp2
    assert exp2.name == new_exp_name


def test_replace_experiment_names_do_not_replace(mlflow_context):
    model_name, exp = _create_model(mlflow_context.client_src)
    export_models(
        model_names = [ model_name ],
        mlflow_client = mlflow_context.client_src,
        output_dir = mlflow_context.output_dir, 
    )
    new_exp_name = mk_uuid()
    import_all(
        mlflow_client = mlflow_context.client_dst,
        input_dir = mlflow_context.output_dir,
        delete_model = True,
        experiment_name_replacements = { "foo": new_exp_name } 
    )
    exp2 = mlflow_context.client_dst.get_experiment_by_name(exp.name)
    assert exp2
    exp2 = mlflow_context.client_dst.get_experiment_by_name(new_exp_name)
    assert not exp2


# == Test import with model replacement tests

def test_replace_model_names_do_replace(mlflow_context):
    model_name = create_model(mlflow_context.client_src)
    export_models(
        model_names = [ model_name ],
        mlflow_client = mlflow_context.client_src,
        output_dir = mlflow_context.output_dir, 
    )
    new_model_name = mk_uuid()
    import_all(
        mlflow_client = mlflow_context.client_dst,
        input_dir = mlflow_context.output_dir,
        delete_model = True,
        model_name_replacements = { model_name: new_model_name }
    )
    model2 = _get_registered_model(mlflow_context.client_dst, model_name)
    assert not model2
    model2 = _get_registered_model(mlflow_context.client_dst, new_model_name)
    assert model2
    assert model2.name == new_model_name

def test_replace_model_names_do_not_replace(mlflow_context):
    model_name = create_model(mlflow_context.client_src)
    export_models(
        model_names = [ model_name ],
        mlflow_client = mlflow_context.client_src,
        output_dir = mlflow_context.output_dir, 
    )
    new_model_name = mk_uuid()
    import_all(
        mlflow_client = mlflow_context.client_dst,
        input_dir = mlflow_context.output_dir,
        delete_model = True,
        model_name_replacements = { "foo": new_model_name }
    )
    model2 = _get_registered_model(mlflow_context.client_dst, model_name)
    assert model2
    model2 = _get_registered_model(mlflow_context.client_dst, new_model_name)
    assert not model2


def _get_registered_model(client, model_name):
    try:
        return client.get_registered_model(model_name)
    except RestException as e:
        #print("ERROR.1: e:",e)
        #print("ERROR.2: e:",e.__dict__.keys())
        #print("ERROR.3: e:error_code:",e.error_code)
        if e.error_code == "RESOURCE_DOES_NOT_EXIST":
            return None
        raise e
