'''
State-centric EKF (Extended Kalman Filter) for use with either an NCP
(Nearly-Constant Position) or NCV (Nearly-Constant Velocity) target dynamic
model.

MIT License

Copyright (c) 2018 Standard Cognition

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''
# pylint: disable=W0611

# Standard
from typing import Optional, Tuple

# Scientific
import numpy as np

# Custom
import stats_tools as stm
import dynamic_models as dmm
import measurements as mm


class EKFState:
    '''
    State-Centric EKF (Extended Kalman Filter) for use with either an NCP
    (Nearly-Constant Position) or NCV (Nearly-Constant Velocity) target dynamic
    model. Stores a target dynamic model, state estimate, and state time.
    Incoming `Measurement`s provide sensor information for updates.

    ::CAUTION:: For efficiency, the dynamic model is only shallow-copied. Make
    a deep copy outside as necessary to protect against unexpected changes.

    Instance Variables:
        _dynamic_model: target dynamic model.
        _mean: state estimate mean.
        _cov: state estimate covariance.
        _time: optional continuous state time. `None` => `_frame_num` should
          not be `None`.
        _frame_num: optional discrete state time. `None` => `_time` should not
          be `None`.

        Cached:
            _mean_pv_cache: state estimate mean cast into PV space.
            _cov_pv_cache: state estimate covariance cast into PV space.

    Args:
        dynamic_model: target dynamic model.
        mean: mean of target state estimate.
        cov: covariance of target state estimate.
        time: optional continuous time of state estimate. If this is not
          provided, `frame_num` must be.
        frame_num: optional discrete time of state estimate. If this is not
          provided, `time` must be.
    '''
    def __init__(
            self, dynamic_model: 'dmm.DifferentiableDynamicModel',
            mean: np.ndarray = None, cov: np.ndarray = None,
            time: float = None, frame_num: int = None) -> None:
        self._dynamic_model = dynamic_model
        if mean is None:
            self._mean = None
        else:
            self._mean = mean.copy()
            self._mean.flags.writeable = False
        if cov is None:
            self._cov = None
        else:
            self._cov = cov.copy()
            self._cov.flags.writeable = False

        assert not (time is None and frame_num is None), \
            'Must provided either `time` or `frame_num`!'
        self._time = time
        self._frame_num = frame_num

        self._mean_pv_cache = None
        self._cov_pv_cache = None

    def _clear_cached(self):
        '''
        Call this whenever actions are taken which invalidate cached data.
        '''
        self._mean_pv_cache = None
        self._cov_pv_cache = None

    @property
    def dynamic_model(self) -> 'dmm.DifferentiableDynamicModel':
        '''Dynamic model access.'''
        return self._dynamic_model

    @property
    def dimension(self) -> int:
        '''Native state dimension access.'''
        return self._dynamic_model.dimension

    @property
    def mean(self) -> np.ndarray:
        '''Native state estimate mean access.'''
        return self._mean

    @property
    def cov(self) -> np.ndarray:
        '''Native state estimate covariance access.'''
        return self._cov

    @property
    def dimension_pv(self) -> int:
        '''PV state dimension access.'''
        return self._dynamic_model.dimension_pv

    @property
    def mean_pv(self) -> np.ndarray:
        '''Compute and return cached PV state estimate mean.'''
        if self._mean_pv_cache is None:
            self._mean_pv_cache = \
                self._dynamic_model.mean2pv(self._mean)
            self._mean_pv_cache.flags.writeable = False

        return self._mean_pv_cache

    @property
    def cov_pv(self) -> np.ndarray:
        '''Compute and return cached PV state estimate covariance.'''
        if self._cov_pv_cache is None:
            self._cov_pv_cache = \
                self._dynamic_model.cov2pv(self._cov)
            self._cov_pv_cache.flags.writeable = False

        return self._cov_pv_cache

    @property
    def time(self) -> Optional[float]:
        '''Continuous state time access.'''
        return self._time

    @property
    def frame_num(self) -> Optional[int]:
        '''Discrete state time access.'''
        return self._frame_num

    def init(
            self, mean: np.ndarray, cov: np.ndarray,
            time: float = None, frame_num: int = None):
        '''
        Re-initialize target state.

        Args:
            mean: target state mean.
            cov: target state covariance.
            time: continuous state time. None => keep existing.
            frame_num: discrete state time. None => keep existing.
        '''
        self._mean = mean.copy()
        self._mean.flags.writeable = False
        self._cov = cov.copy()
        self._cov.flags.writeable = False
        if time is not None:
            self._time = time
        if frame_num is not None:
            self._frame_num = frame_num

        self._clear_cached()

    def predict(
            self, dt: float,
            destination_time: float = None,
            destination_frame_num: int = None):
        '''
        Use dynamic model to predict (aka propagate aka integrate) state
        estimate in-place.

        Args:
            dt: time to integrate over. To prevent accumulation of roundoff
              error, either `destination_time` or `destination_frame_num` must
              be provided separately.
            destination_time: optional value to set continuous state time to
              after integration. If this is not provided, then
              `destination_frame_num` must be.
            destination_frame_num: optional value to set discrete state time to
              after integration. If this is not provided, then
              `destination_time` must be.
        '''
        self._mean = self._dynamic_model(self._mean, dt)
        self._mean.flags.writeable = False

        F = self._dynamic_model.jacobian(dt)
        Q = self._dynamic_model.process_noise_cov(dt)
        self._cov = F.dot(self._cov).dot(F.T) + Q
        self._cov.flags.writeable = False

        assert not (
            destination_time is None and destination_frame_num is None), \
            'Must provided either `destination_time` ' \
            'or `destination_frame_num`!'
        self._time = destination_time
        self._frame_num = destination_frame_num

        self._clear_cached()

    def innovation(self, measurement: 'mm.Measurement') -> Tuple[np.ndarray]:
        '''
        Compute and return the innovation that a measurement would induce if
        it were used for an update, but don't actually perform the update.
        Assumes state and measurement are time-aligned. Useful for computing
        Chi^2 stats and likelihoods.

        Args:
            measurement.

        Returns:
            Innovation mean and covariance of hypothetical update.
        '''
        assert self._time == measurement.time, \
            'State time and measurement time must be aligned!'

        # Compute innovation.
        x_pv = self._dynamic_model.mean2pv(self._mean)
        H = measurement.jacobian(x_pv)[:, :self.dimension]
        R = measurement.cov
        z = measurement.mean
        z_predicted = measurement(x_pv)
        dz = measurement.geodesic_difference(z, z_predicted)
        S = H.dot(self._cov).dot(H.T) + R  # innovation cov

        return dz, S

    def chi2_stat_of_update(self, measurement: 'mm.Measurement') -> float:
        '''
        Compute and return the Chi^2 stat of a potential update, but don't
        actually perform the update. Assumes state and measurement are time-
        aligned. Useful for gating, and calculating costs in assignment
        problems for data association.

        Args:
            measurement.

        Returns:
            Chi^2 stat of hypothetical update.
        '''
        dz, S = self.innovation(measurement)
        return dz.dot(np.linalg.solve(S, dz))

    def likelihood_of_update(self, measurement: 'mm.Measurement') -> float:
        '''
        Compute and return the likelihood of a potential update, but don't
        actually perform the update. Assumes state and measurement are time-
        aligned. Useful for gating and calculating costs in assignment problems
        for data association.

        Args:
            measurement.

        Returns:
            Likelihood of hypothetical update.
        '''
        dz, S = self.innovation(measurement)
        return stm.evaluate_normal_pdf(dz, S)

    def update(self, measurement: 'mm.Measurement') -> Tuple[np.ndarray]:
        '''
        Use measurement to update state estimate in-place and return
        innovation. The innovation is useful, e.g., for evaluating filter
        consistency or updating model likelihoods when the `EKFState` is part
        of an `IMMFState`.

        Args:
            measurement.

        Returns:
            Innovation mean and covariance.
        '''
        if self._time is not None:
            assert self._time == measurement.time, \
                'State time and measurement time must be aligned!'
        if self._frame_num is not None:
            assert self._frame_num == measurement.frame_num, \
                'State time and measurement time must be aligned!'

        x = self._mean
        x_pv = self._dynamic_model.mean2pv(x)
        P = self.cov
        H = measurement.jacobian(x_pv)[:, :self.dimension]
        R = measurement.cov
        z = measurement.mean
        z_predicted = measurement(x_pv)
        dz = measurement.geodesic_difference(z, z_predicted)
        S = H.dot(P).dot(H.T) + R  # innovation cov

        K_prefix = self._cov.dot(H.T)
        dx = K_prefix.dot(np.linalg.solve(S, dz))  # K*dz
        x = self._dynamic_model.geodesic_difference(x, -dx)

        I = np.eye(self._dynamic_model.dimension)
        ImKH = I - K_prefix.dot(np.linalg.solve(S, H))
        # *Joseph form* of covariance update for numerical stability.
        P = ImKH.dot(self.cov).dot(ImKH.T) \
            + K_prefix.dot(np.linalg.solve(S, \
            (K_prefix.dot(np.linalg.solve(S, R))).T))

        self._mean = x
        self._mean.flags.writeable = False
        self._cov = P
        self._cov.flags.writeable = False

        self._clear_cached()

        return dz, S

    def copy(self, time: float = None, frame_num: int = None) -> 'EKFState':
        '''
        Deepcopy everything, except dynamic model is only shallow-copied.

        Optionally `time` and/or `frame_num` can be reset. This is useful,
        e.g., if you want to cache an intial filter state that can be copied
        into newly initialized tracks at different times.

        Args:
            time: optional new continuous state time.
            frame_num: optional new discrete state time.

        Returns:
            new filter state
        '''
        if time is not None and frame_num is None:
            return EKFState(
                dynamic_model=self._dynamic_model,
                mean=self._mean, cov=self._cov,
                time=time, frame_num=None)

        if time is None and frame_num is not None:
            return EKFState(
                dynamic_model=self._dynamic_model,
                mean=self._mean, cov=self._cov,
                time=None, frame_num=frame_num)

        return EKFState(
            dynamic_model=self._dynamic_model,
            mean=self._mean, cov=self._cov,
            time=self._time, frame_num=self._frame_num)
