import warnings

import numpy as np

from src.module.helper import timeit

warnings.filterwarnings('ignore')


@timeit
def reduce_mem_usage(data, verbose=True):
    """Memory Reducer signed

    Args:
        data: pandas dataframe to reduce size
        verbose (bool)
    Returns:
        data

    """
    numerics = ['int16', 'int32', 'int64', 'float16', 'float32', 'float64']
    start_mem = data.memory_usage().sum() / 1024**2
    for col in data.columns:
        col_type = data[col].dtypes
        if col_type in numerics:
            c_min = data[col].min()
            c_max = data[col].max()
            if str(col_type)[:3] == 'int':
                if c_min > np.iinfo(np.int8).min and c_max < np.iinfo(np.int8).max:
                    data[col] = data[col].astype(np.int8)
                elif c_min > np.iinfo(np.int16).min and c_max < np.iinfo(np.int16).max:
                    data[col] = data[col].astype(np.int16)
                elif c_min > np.iinfo(np.int32).min and c_max < np.iinfo(np.int32).max:
                    data[col] = data[col].astype(np.int32)
                elif c_min > np.iinfo(np.int64).min and c_max < np.iinfo(np.int64).max:
                    data[col] = data[col].astype(np.int64)
            else:
                if (
                    c_min > np.finfo(np.float16).min
                    and c_max < np.finfo(np.float16).max
                ):
                    data[col] = data[col].astype(np.float16)
                elif (
                    c_min > np.finfo(np.float32).min
                    and c_max < np.finfo(np.float32).max
                ):
                    data[col] = data[col].astype(np.float32)
                else:
                    data[col] = data[col].astype(np.float64)
    end_mem = data.memory_usage().sum() / 1024**2
    if verbose:
        print(
            'Mem. usage decreased from {:5.2f} to {:5.2f} Mb ({:.1f}% reduction)'.format(
                start_mem, end_mem, 100 * (start_mem - end_mem) / start_mem
            )
        )
    return data


@timeit
def reduce_mem_usage_unsigned(data, verbose=True):
    """Memory Reducer unsigned

    Args:
        data: pandas dataframe to reduce size
        verbose (bool)
    Returns:
        data

    """
    numerics = ['int16', 'int32', 'int64', 'float16', 'float32', 'float64']
    start_mem = data.memory_usage().sum() / 1024**2
    for col in data.columns:
        col_type = data[col].dtypes
        if col_type in numerics:
            c_min = data[col].min()
            c_max = data[col].max()
            if str(col_type)[:3] == 'int':
                if c_min > np.iinfo(np.uint8).min and c_max < np.iinfo(np.uint8).max:
                    data[col] = data[col].astype(np.int8)
                elif (
                    c_min > np.iinfo(np.uint16).min and c_max < np.iinfo(np.uint16).max
                ):
                    data[col] = data[col].astype(np.uint16)
                elif (
                    c_min > np.iinfo(np.uint32).min and c_max < np.iinfo(np.uint32).max
                ):
                    data[col] = data[col].astype(np.int32)
                elif (
                    c_min > np.iinfo(np.uint64).min and c_max < np.iinfo(np.uint64).max
                ):
                    data[col] = data[col].astype(np.int64)
            else:
                if (
                    c_min > np.finfo(np.float16).min
                    and c_max < np.finfo(np.float16).max
                ):
                    data[col] = data[col].astype(np.float16)
                elif (
                    c_min > np.finfo(np.float32).min
                    and c_max < np.finfo(np.float32).max
                ):
                    data[col] = data[col].astype(np.float32)
                else:
                    data[col] = data[col].astype(np.float64)
    end_mem = data.memory_usage().sum() / 1024**2
    if verbose:
        print(
            'Mem. usage decreased from {:5.2f} to {:5.2f} Mb ({:.1f}% reduction)'.format(
                start_mem, end_mem, 100 * (start_mem - end_mem) / start_mem
            )
        )
    return data
