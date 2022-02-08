from abc import ABC, abstractmethod
import os
from threading import Lock
from typing import Any, Callable, Optional, Sequence
import warnings

from .mmap_dict import mmap_key, MmapedDict
from .samples import Exemplar


class Value(ABC):
    @abstractmethod
    def __init__(self,
                 typ: Optional[str],
                 metric_name: str,
                 name: str,
                 labelnames: Sequence[str],
                 labelvalues: Sequence[str],
                 **kwargs: Any,
                 ):
        pass

    @abstractmethod
    def inc(self, amount: float) -> None:
        pass

    @abstractmethod
    def set(self, value: float) -> None:
        pass

    @abstractmethod
    def set_exemplar(self, exemplar: Exemplar) -> None:
        pass

    @abstractmethod
    def get(self) -> float:
        pass

    @abstractmethod
    def get_exemplar(self) -> Optional[Exemplar]:
        pass


class MutexValue(Value):
    """A float protected by a mutex."""

    _multiprocess = False

    def __init__(self,
                 typ: Optional[str],
                 metric_name: str,
                 name: str,
                 labelnames: Sequence[str],
                 labelvalues: Sequence[str],
                 **kwargs: Any,
                 ):
        self._value = 0.0
        self._exemplar: Optional[Exemplar] = None
        self._lock = Lock()

    def inc(self, amount: float) -> None:
        with self._lock:
            self._value += amount

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def set_exemplar(self, exemplar: Exemplar) -> None:
        with self._lock:
            self._exemplar = exemplar

    def get(self) -> float:
        with self._lock:
            return self._value

    def get_exemplar(self) -> Optional[Exemplar]:
        with self._lock:
            return self._exemplar


def MultiProcessValue(process_identifier: Callable[[], int] = os.getpid) -> type[Value]:
    """Returns a MmapedValue class based on a process_identifier function.

    The 'process_identifier' function MUST comply with this simple rule:
    when called in simultaneously running processes it MUST return distinct values.

    Using a different function than the default 'os.getpid' is at your own risk.
    """
    files = {}
    values = []
    pid = {'value': process_identifier()}
    # Use a single global lock when in multi-processing mode
    # as we presume this means there is no threading going on.
    # This avoids the need to also have mutexes in __MmapDict.
    lock = Lock()

    class MmapedValue(Value):
        """A float protected by a mutex backed by a per-process mmaped file."""

        _multiprocess = True

        def __init__(self, typ, metric_name, name, labelnames, labelvalues, multiprocess_mode='', **kwargs):
            self._params = typ, metric_name, name, labelnames, labelvalues, multiprocess_mode
            # This deprecation warning can go away in a few releases when removing the compatibility
            if 'prometheus_multiproc_dir' in os.environ and 'PROMETHEUS_MULTIPROC_DIR' not in os.environ:
                os.environ['PROMETHEUS_MULTIPROC_DIR'] = os.environ['prometheus_multiproc_dir']
                warnings.warn("prometheus_multiproc_dir variable has been deprecated in favor of the upper case naming PROMETHEUS_MULTIPROC_DIR", DeprecationWarning)
            with lock:
                self.__check_for_pid_change()
                self.__reset()
                values.append(self)

        def __reset(self):
            typ, metric_name, name, labelnames, labelvalues, multiprocess_mode = self._params
            if typ == 'gauge':
                file_prefix = typ + '_' + multiprocess_mode
            else:
                file_prefix = typ
            if file_prefix not in files:
                filename = os.path.join(
                    os.environ.get('PROMETHEUS_MULTIPROC_DIR'),
                    '{}_{}.db'.format(file_prefix, pid['value']))

                files[file_prefix] = MmapedDict(filename)
            self._file = files[file_prefix]
            self._key = mmap_key(metric_name, name, labelnames, labelvalues)
            self._value = self._file.read_value(self._key)

        def __check_for_pid_change(self):
            actual_pid = process_identifier()
            if pid['value'] != actual_pid:
                pid['value'] = actual_pid
                # There has been a fork(), reset all the values.
                for f in files.values():
                    f.close()
                files.clear()
                for value in values:
                    value.__reset()

        def inc(self, amount):
            with lock:
                self.__check_for_pid_change()
                self._value += amount
                self._file.write_value(self._key, self._value)

        def set(self, value):
            with lock:
                self.__check_for_pid_change()
                self._value = value
                self._file.write_value(self._key, self._value)

        def set_exemplar(self, exemplar):
            # TODO: Implement exemplars for multiprocess mode.
            return

        def get(self):
            with lock:
                self.__check_for_pid_change()
                return self._value

        def get_exemplar(self):
            # TODO: Implement exemplars for multiprocess mode.
            return None

    return MmapedValue


def get_value_class() -> type[Value]:
    # Should we enable multi-process mode?
    # This needs to be chosen before the first metric is constructed,
    # and as that may be in some arbitrary library the user/admin has
    # no control over we use an environment variable.
    if 'prometheus_multiproc_dir' in os.environ or 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
        return MultiProcessValue()
    else:
        return MutexValue


ValueClass = get_value_class()
