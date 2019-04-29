# A module to filter and display meta information
import os
import os.path as osp
import numpy as np
import pandas as pd
import textwrap
from sqlalchemy import create_engine
import tempfile
from git import Repo
from empd_admin.common import read_empd_meta


def query_samples(meta_df, query):

    # create a temporary sqlite database to execute the query
    with tempfile.TemporaryDirectory('_empd') as tmpdir:
        engine = create_engine(f'sqlite:///{tmpdir}/meta.sqlite')
        meta_df.to_sql('meta', engine)
        samples = pd.read_sql(
            f"SELECT SampleName FROM meta WHERE {query}",
            engine).SampleName.values
    return samples


def query_meta(meta, query, columns='notnull', count=False,
               output=None, commit=False, local_repo=None,
               distinct=False):
    if local_repo is None:
        local_repo = osp.dirname(meta)
    else:
        meta = osp.join(local_repo, meta)
    meta_df = read_empd_meta(meta).replace('', np.nan)
    samples = query_samples(meta_df, query)

    sub = meta_df.loc[samples].reset_index()
    if isinstance(columns, str):
        columns = [columns]

    if 'notnull' in columns:
        missing = []
        notnull = sub.notnull().any(axis=0)
        columns = notnull[notnull].index
    elif 'all' in columns:
        missing = []
        columns = sub.columns
    else:
        columns = np.array(columns)
        mask = np.isin(columns, sub.columns)
        missing = columns[~mask]
        columns = columns[mask]
    if count:
        sub = sub[columns].count().to_frame().reset_index().fillna('')
        sub.columns = ['Column', 'Count']
    else:
        sub = sub[columns].fillna('')
    if commit:
        output = output or 'query.tsv'
    if output:
        ofile = osp.join(local_repo, 'queries', output)
        os.makedirs(osp.dirname(ofile), exist_ok=True)
        sub.to_csv(ofile, '\t', float_format='%1.8g')

    if commit:
        repo = Repo(local_repo)
        repo.index.add([osp.join('queries', output)])
        repo.index.commit(f'Added {output} [skip ci]\n\n{query}')

    sub = pd.concat([
        pd.DataFrame([('---', ) * len(sub.columns)], columns=sub.columns),
        sub], ignore_index=True)

    if distinct:
        if 'all' in distinct:
            distinct = sub.columns
        sub.drop_duplicates(distinct, inplace=True)

    ret = f'<details><summary>{query}</summary>\n\n' + textwrap.indent(
        sub.head(200).to_csv(sep='|', index=False, float_format='%1.8g'), '| ')
    ret += '\n\nDisplaying %i of %i rows' % (min(len(sub) - 1, 200),
                                             len(sub) - 1)
    if len(missing):
        ret += '\n\nMissing columns ' + ', '.join(missing)
    return output, ret + '\n</details>'
