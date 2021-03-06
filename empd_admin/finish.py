"""Command to finish the merging of a PR"""
import os
import os.path as osp
import shutil
import pandas as pd
from git import Repo
from empd_admin.repo_test import (
    import_database, temporary_database, get_meta_file,
    run_test, remember_cwd, fetch_upstream)
import subprocess as spr
import textwrap
from empd_admin.common import read_empd_meta, get_psql_scripts, dump_empd_meta


def finish_pr(meta, commit=True):
    """Finish a data contribution to the EMPD

    This functions is supposed to be called at the end of a new data
    contribution in a github pull request

    Parameters
    ----------
    meta: str
        The path to the meta file
    commit: bool
        If True, commit the changes to the git repository of `meta`
    """
    rebase_master(meta)
    fix_sample_formats(meta, commit)
    merge_postgres(meta, commit=commit)
    merge_meta(meta, commit=commit)

    with remember_cwd():
        os.chdir(osp.dirname(meta))
        repo = Repo('.')

        if commit and osp.exists('failures'):
            repo.git.rm('-r', 'failures')
            repo.index.commit("Removed extracted failures")

        if commit and osp.exists('queries'):
            repo.git.rm('-r', 'queries')
            repo.index.commit("Removed extracted queries")

        if commit and osp.basename(meta) != 'meta.tsv':
            repo.git.rm(osp.basename(meta))
            repo.index.commit(
                "Removed %s to finish the PR" % osp.basename(meta))
    return


def merge_meta(meta, target=None, commit=True, local_repo=None):
    """Merge one EMPD meta data into another

    Parameters
    ----------
    meta: str
        The file to merge.
    target: str
        The file to merge `meta` into. If None, the meta file of the
        `local_repo` is used, and, if this is `meta` we use `meta.tsv`.
    commit: bool
        If True, commit the changes to the git repository
    local_repo: str
        The path to the EMPD-data local repository. If None, the directory of
        `meta` is used

    Returns
    -------
    str
        The `target`"""
    if local_repo is None:
        local_repo = osp.dirname(meta)

    if not target:
        target = osp.basename(get_meta_file(local_repo))
        if osp.samefile(meta, osp.join(local_repo, target)):
            target = 'meta.tsv'

    meta_df = read_empd_meta(meta)

    base_meta = osp.join(local_repo, target)
    base_meta_df = read_empd_meta(base_meta)

    # update the meta file and save
    base_meta_df = base_meta_df.join(meta_df[[]], how='outer')
    cols = [col for col in meta_df.columns if col in base_meta_df.columns]
    base_meta_df.loc[meta_df.index, cols] = meta_df

    dump_empd_meta(base_meta_df, base_meta)

    if commit:
        repo = Repo(local_repo)
        repo.index.add([target])
        repo.index.commit("Merged {} into {} [skip ci]".format(
            osp.basename(meta), target))

    return target


def rebase_master(meta):
    """Merge the master branch of EMPD2/EMPD-data into the local fork

    Parameters
    ----------
    meta: str
        The path to the meta file of the data contribution"""
    # Merge the master branch into the feature branch using rebase
    repo = Repo(osp.dirname(meta))
    fetch_upstream(repo)
    repo.git.pull('upstream', 'master')


def fix_sample_formats(meta, commit=True):
    """Fix the sample formats changing the order, etc.

    Parameters
    ----------
    meta: str
        The path to the meta file of the data contribution
    commit: bool
        If True, commit the changes to the git repository"""
    pytest_args = ['--fix-db', '-v', '-k', 'fix_sample_data_formatting']
    if commit:
        pytest_args.append('--commit')

    success, log, md = run_test(meta, pytest_args, ['fixes.py'])
    assert success, log


def merge_postgres(meta, commit=True):
    """Merge the new metadata into the EMPD2 postgres database

    Parameters
    ----------
    meta: str
        The path to the meta file of the data contribution
    commit: bool
        If True, commit the changes to the git repository"""
    # import the data into the EMPD2 database
    if commit:
        with remember_cwd():
            os.chdir(osp.dirname(meta))

            success, msg, dump = import_database(
                meta, commit=True,
                populate=osp.join('postgres', 'EMPD2.sql'),
                sql_dump=osp.join('postgres', 'EMPD2.sql'))

            assert success, msg

            repo = Repo('.')
            old_sql_dump = osp.join(
                'postgres', osp.splitext(osp.basename(meta))[0] + '.sql')
            if osp.exists(old_sql_dump):
                repo.git.rm(osp.join('postgres', osp.basename(old_sql_dump)))
                repo.index.commit(
                    "Removed postgres dump of %s" % osp.basename(meta))

            # export database as tab-delimited tables
            tables_dir = 'tab-delimited'
            with temporary_database() as db_url:
                spr.check_call(['psql', db_url, '-q', '-f', dump],
                               stdout=spr.DEVNULL)
                query = ("SELECT tablename FROM pg_tables "
                         "WHERE schemaname='public'")
                tables = spr.check_output(
                    ['psql', db_url, '-Atc', query]).decode('utf-8').split()
                copy = ("COPY public.%s TO STDOUT "
                        "WITH CSV HEADER DELIMITER E'\\t'")
                for table in tables:
                    cmd = ['psql', db_url, '-q', '-c', copy % table, '-o',
                           osp.join(tables_dir, table + '.tsv')]
                    spr.check_call(cmd)
                repo.index.add([osp.join(tables_dir, table + '.tsv')
                                for table in tables])
                repo.index.commit(
                    "Updated tab-delimited files from EMPD2 postgres database")

    else:
        # to dump it to a temporary file
        success, msg, dump = import_database(meta, commit=True)

        assert success, msg


def look_for_changed_fixed_tables(meta, pr_owner, pr_repo, pr_branch):
    """Check whether any of the fixed tables has been changed

    The import of the data contribution into the postgres database might add
    new entries into the postgres/scripts/tables files. This function checks
    for this and reports back to the PR

    Parameters
    ----------
    meta: str
        The path to the meta file of the data contribution
    pr_owner: str
        The owner (github username) of the data contribution
    pr_repo: str
        The name of the repository
    pr_branch: str
        The branch of the data contribution

    Returns
    -------
    str
        The status message to report what happened with the fixed tables"""
    fixed = ['Country', 'GroupID', 'SampleContext', 'SampleMethod',
             'SampleType']
    msg = ''
    changed_tables = []
    local_tables = osp.join(osp.dirname(meta), 'postgres', 'scripts', 'tables')
    for table in fixed:
        fname = osp.join(get_psql_scripts(), 'tables', table + '.tsv')
        old = pd.read_csv(fname, sep='\t')
        new = pd.read_csv(osp.join(local_tables, table + '.tsv'), sep='\t')
        changed = set(map(tuple, new.values)) - set(map(tuple, old.values))
        if changed:
            shutil.copyfile(osp.join(local_tables, table + '.tsv'), fname)
            changed = pd.DataFrame(
                [('---', ) * len(new.columns)] + list(changed),
                columns=new.columns)
            changed_tables.append(table)
            msg += textwrap.dedent(f"""
                - postgres/scripts/tables/{table}.tsv - [Edit the file](https://github.com/{pr_owner}/{pr_repo}/edit/{pr_branch}/postgres/scripts/tables/{table}.tsv)

                  <details><summary>%i changed rows:</summary>

                  %s
                  </details>
                """) % (len(changed) - 1,
                        textwrap.indent(dump_empd_meta(changed, sep='|'),
                                        '  | '))
    if changed_tables:
        if len(changed_tables) == 1:
            msg = ("**Note** that one of the fixed tables has been changed!"
                   "\n\n%s\n\nPlease review it. """) % msg
        else:
            msg = ("**Note** that some of the fixed tables have been changed!"
                   "\n\n%s\n\nPlease review them. ") % msg
        action_required = set(changed_tables) & {
            'GroupID', 'SampleType', 'Country'}
        if action_required:
            suffix = 's' if len(action_required) > 1 else ''
            msg += ("If you change the file%s, please tell me via\n"
                    "`@EMPD-admin rebuild %s`\n"
                    "to update the table%s in the database") % (
                        suffix, ' '.join(action_required), suffix)
    return msg
