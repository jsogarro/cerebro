"""
Bayesian Experiment Design for Hyperparameter Tuning and Optimization

This module implements Bayesian optimization techniques for experimental design,
providing intelligent parameter selection, prior specification, and posterior updates.
Integrates with PyMC for advanced statistical modeling and Gaussian Process models
for continuous optimization.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import arviz as az
import numpy as np
import pymc as pm
from scipy.optimize import minimize

# Bayesian optimization libraries
from scipy.stats import norm
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, Matern, WhiteKernel

logger = logging.getLogger(__name__)


class AcquisitionFunction(Enum):
    """Acquisition functions for Bayesian optimization."""
    
    EXPECTED_IMPROVEMENT = "expected_improvement"
    UPPER_CONFIDENCE_BOUND = "upper_confidence_bound"
    PROBABILITY_OF_IMPROVEMENT = "probability_of_improvement"
    THOMPSON_SAMPLING = "thompson_sampling"
    ENTROPY_SEARCH = "entropy_search"


class PriorType(Enum):
    """Prior distribution types for parameters."""
    
    UNIFORM = "uniform"
    NORMAL = "normal"
    BETA = "beta"
    GAMMA = "gamma"
    LOG_NORMAL = "log_normal"
    DIRICHLET = "dirichlet"


@dataclass
class ParameterPrior:
    """Prior specification for a single parameter."""

    name: str
    prior_type: PriorType
    bounds: tuple[float, float]
    hyperparameters: dict[str, float] = field(default_factory=dict)
    is_discrete: bool = False
    allowed_values: list[Any] | None = None
    
    def sample(self, n_samples: int = 1) -> np.ndarray:
        """Sample from the prior distribution."""
        if self.prior_type == PriorType.UNIFORM:
            samples = np.random.uniform(
                self.bounds[0], self.bounds[1], n_samples
            )
        elif self.prior_type == PriorType.NORMAL:
            mean = self.hyperparameters.get("mean", 0)
            std = self.hyperparameters.get("std", 1)
            samples = np.random.normal(mean, std, n_samples)
            samples = np.clip(samples, self.bounds[0], self.bounds[1])
        elif self.prior_type == PriorType.BETA:
            alpha = self.hyperparameters.get("alpha", 2)
            beta_param = self.hyperparameters.get("beta", 2)
            samples = np.random.beta(alpha, beta_param, n_samples)
            # Scale to bounds
            samples = samples * (self.bounds[1] - self.bounds[0]) + self.bounds[0]
        elif self.prior_type == PriorType.GAMMA:
            shape = self.hyperparameters.get("shape", 2)
            scale = self.hyperparameters.get("scale", 1)
            samples = np.random.gamma(shape, scale, n_samples)
            samples = np.clip(samples, self.bounds[0], self.bounds[1])
        elif self.prior_type == PriorType.LOG_NORMAL:
            mean = self.hyperparameters.get("mean", 0)
            std = self.hyperparameters.get("std", 1)
            samples = np.random.lognormal(mean, std, n_samples)
            samples = np.clip(samples, self.bounds[0], self.bounds[1])
        else:
            raise ValueError(f"Unsupported prior type: {self.prior_type}")
        
        if self.is_discrete and self.allowed_values:
            # Map to nearest allowed value
            samples_list = [
                min(self.allowed_values, key=lambda x: abs(x - s)) for s in samples
            ]
            samples = np.array(samples_list)
        
        return samples


@dataclass
class BayesianOptimizationResult:
    """Result from Bayesian optimization."""

    best_params: dict[str, float]
    best_value: float
    all_params: list[dict[str, float]]
    all_values: list[float]
    convergence_history: list[float]
    posterior_samples: np.ndarray | None = None
    acquisition_values: np.ndarray | None = None
    gp_model: GaussianProcessRegressor | None = None


class BayesianExperimentDesigner:
    """
    Bayesian optimization for experimental design and hyperparameter tuning.
    
    This class provides sophisticated Bayesian methods for optimizing experiment
    parameters, including Gaussian Process models, various acquisition functions,
    and integration with PyMC for complex statistical modeling.
    """
    
    def __init__(
        self,
        parameter_priors: list[ParameterPrior],
        objective_function: Callable[..., Any] | None = None,
        acquisition_function: AcquisitionFunction = AcquisitionFunction.EXPECTED_IMPROVEMENT,
        kernel_type: str = "matern",
        n_initial_points: int = 5,
        random_state: int = 42,
    ) -> None:
        """
        Initialize Bayesian experiment designer.
        
        Args:
            parameter_priors: Prior specifications for parameters
            objective_function: Function to optimize (can be set later)
            acquisition_function: Acquisition function for optimization
            kernel_type: GP kernel type ("matern", "rbf", "combined")
            n_initial_points: Number of initial random samples
            random_state: Random seed for reproducibility
        """
        self.parameter_priors = {p.name: p for p in parameter_priors}
        self.objective_function = objective_function
        self.acquisition_function = acquisition_function
        self.n_initial_points = n_initial_points
        self.random_state = random_state
        
        # Initialize Gaussian Process
        self.gp_model = self._create_gp_model(kernel_type)
        
        # Storage for observations
        self.X_observed: list[np.ndarray] = []
        self.y_observed: list[float] = []

        # Optimization history
        self.optimization_history: list[dict[str, Any]] = []
        
        np.random.seed(random_state)
    
    def _create_gp_model(self, kernel_type: str) -> GaussianProcessRegressor:
        """Create Gaussian Process model with specified kernel."""
        if kernel_type == "matern":
            kernel = Matern(length_scale=1.0, nu=2.5) + WhiteKernel(noise_level=1e-5)
        elif kernel_type == "rbf":
            kernel = RBF(length_scale=1.0) + WhiteKernel(noise_level=1e-5)
        elif kernel_type == "combined":
            kernel = (Matern(length_scale=1.0, nu=2.5) * 
                     RBF(length_scale=1.0) + 
                     WhiteKernel(noise_level=1e-5))
        else:
            raise ValueError(f"Unknown kernel type: {kernel_type}")
        
        return GaussianProcessRegressor(
            kernel=kernel,
            n_restarts_optimizer=10,
            alpha=1e-6,
            normalize_y=True,
            random_state=self.random_state
        )
    
    async def optimize(
        self,
        n_iterations: int = 50,
        objective_function: Callable[..., Any] | None = None,
        parallel_evaluations: int = 1,
        convergence_threshold: float = 1e-4,
    ) -> BayesianOptimizationResult:
        """
        Run Bayesian optimization to find optimal parameters.
        
        Args:
            n_iterations: Number of optimization iterations
            objective_function: Function to optimize (overrides constructor)
            parallel_evaluations: Number of parallel function evaluations
            convergence_threshold: Threshold for convergence detection
            
        Returns:
            BayesianOptimizationResult with optimization results
        """
        obj_func = objective_function or self.objective_function
        if not obj_func:
            raise ValueError("No objective function provided")
        
        # Initial sampling from priors
        if len(self.X_observed) < self.n_initial_points:
            await self._initial_sampling(obj_func, self.n_initial_points)
        
        # Main optimization loop
        for iteration in range(n_iterations):
            # Fit GP model to observations
            if len(self.X_observed) > 0:
                X = np.array(self.X_observed)
                y = np.array(self.y_observed)
                self.gp_model.fit(X, y)
            
            # Get next points to evaluate
            next_points = await self._get_next_points(parallel_evaluations)
            
            # Evaluate objective function
            if parallel_evaluations > 1:
                # Parallel evaluation
                tasks = [self._evaluate_async(obj_func, point) 
                        for point in next_points]
                results = await asyncio.gather(*tasks)
            else:
                # Sequential evaluation
                results = [await self._evaluate_async(obj_func, next_points[0])]
            
            # Update observations
            for point, value in zip(next_points, results):
                self.X_observed.append(point)
                self.y_observed.append(value)
                self.optimization_history.append({
                    "iteration": iteration,
                    "params": self._array_to_dict(point),
                    "value": value
                })
            
            # Check convergence
            if self._check_convergence(convergence_threshold):
                logger.info(f"Converged after {iteration} iterations")
                break
            
            # Log progress
            if iteration % 10 == 0:
                best_idx = np.argmax(self.y_observed)
                logger.info(f"Iteration {iteration}: Best value = {self.y_observed[best_idx]}")
        
        # Get best result
        best_idx = np.argmax(self.y_observed)
        best_params = self._array_to_dict(self.X_observed[best_idx])
        best_value = self.y_observed[best_idx]
        
        # Get posterior samples
        posterior_samples = self._sample_posterior(n_samples=1000)
        
        return BayesianOptimizationResult(
            best_params=best_params,
            best_value=best_value,
            all_params=[self._array_to_dict(x) for x in self.X_observed],
            all_values=self.y_observed,
            convergence_history=[h["value"] for h in self.optimization_history],
            posterior_samples=posterior_samples,
            gp_model=self.gp_model
        )
    
    async def _initial_sampling(
        self, objective_function: Callable[..., Any], n_points: int
    ) -> None:
        """Generate initial samples from prior distributions."""
        for _ in range(n_points):
            # Sample from priors
            point_list = []
            for name, prior in self.parameter_priors.items():
                value = prior.sample(1)[0]
                point_list.append(value)

            point = np.array(point_list)
            value = await self._evaluate_async(objective_function, point)

            self.X_observed.append(point)
            self.y_observed.append(value)
    
    async def _get_next_points(self, n_points: int) -> list[np.ndarray]:
        """Get next points to evaluate using acquisition function."""
        if len(self.X_observed) == 0:
            # Random sampling if no observations
            points: list[np.ndarray] = []
            for _ in range(n_points):
                point_list = []
                for name, prior in self.parameter_priors.items():
                    value = prior.sample(1)[0]
                    point_list.append(value)
                points.append(np.array(point_list))
            return points

        # Use acquisition function
        points = []
        for _ in range(n_points):
            point = self._optimize_acquisition()
            points.append(point)

        return points
    
    def _optimize_acquisition(self) -> np.ndarray:
        """Optimize acquisition function to get next point."""
        # Define bounds for optimization
        bounds = []
        for name, prior in self.parameter_priors.items():
            bounds.append(prior.bounds)

        # Multiple random starts for optimization
        best_point: np.ndarray | None = None
        best_acquisition = -np.inf

        for _ in range(10):
            # Random starting point
            x0_list = []
            for name, prior in self.parameter_priors.items():
                value = prior.sample(1)[0]
                x0_list.append(value)
            x0 = np.array(x0_list)

            # Optimize acquisition function
            result = minimize(
                lambda x: -self._compute_acquisition(x),
                x0,
                bounds=bounds,
                method="L-BFGS-B",
            )

            if -result.fun > best_acquisition:
                best_acquisition = -result.fun
                best_point = result.x

        return best_point if best_point is not None else np.array([])
    
    def _compute_acquisition(self, x: np.ndarray) -> float:
        """Compute acquisition function value for a point."""
        x_reshaped = x.reshape(1, -1)

        # Get GP predictions
        mu, sigma = self.gp_model.predict(x_reshaped, return_std=True)
        mu_val = float(mu[0])
        sigma_val = float(sigma[0])

        if self.acquisition_function == AcquisitionFunction.EXPECTED_IMPROVEMENT:
            # Expected Improvement
            if len(self.y_observed) > 0:
                best = np.max(self.y_observed)
                z = (mu_val - best) / (sigma_val + 1e-9)
                ei = (mu_val - best) * norm.cdf(z) + sigma_val * norm.pdf(z)
                return float(ei)
            return mu_val

        elif self.acquisition_function == AcquisitionFunction.UPPER_CONFIDENCE_BOUND:
            # Upper Confidence Bound
            beta = 2.0  # Exploration parameter
            return float(mu_val + beta * sigma_val)

        elif self.acquisition_function == AcquisitionFunction.PROBABILITY_OF_IMPROVEMENT:
            # Probability of Improvement
            if len(self.y_observed) > 0:
                best = np.max(self.y_observed)
                z = (mu_val - best) / (sigma_val + 1e-9)
                return float(norm.cdf(z))
            return 0.5
        
        else:
            raise ValueError(f"Unsupported acquisition function: {self.acquisition_function}")
    
    async def _evaluate_async(
        self, objective_function: Callable[..., Any], point: np.ndarray
    ) -> float:
        """Evaluate objective function asynchronously."""
        # Convert array to dict for function call
        params = self._array_to_dict(point)
        
        # Run objective function
        if asyncio.iscoroutinefunction(objective_function):
            value = await objective_function(params)
        else:
            # Run sync function in executor
            loop = asyncio.get_event_loop()
            value = await loop.run_in_executor(None, objective_function, params)
        
        return float(value)

    def _array_to_dict(self, array: np.ndarray) -> dict[str, float]:
        """Convert parameter array to dictionary."""
        params = {}
        for i, name in enumerate(self.parameter_priors.keys()):
            params[name] = float(array[i])
        return params
    
    def _check_convergence(self, threshold: float) -> bool:
        """Check if optimization has converged."""
        if len(self.optimization_history) < 10:
            return False

        # Check if best value hasn't improved significantly
        recent_values = [h["value"] for h in self.optimization_history[-10:]]
        improvement = np.max(recent_values) - np.min(recent_values)

        return bool(improvement < threshold)
    
    def _sample_posterior(self, n_samples: int = 1000) -> np.ndarray:
        """Sample from posterior distribution using GP model."""
        if len(self.X_observed) == 0:
            return np.array([])

        # Create grid for sampling
        bounds = []
        for name, prior in self.parameter_priors.items():
            bounds.append(prior.bounds)

        # Sample from posterior
        samples: list[float] = []
        for _ in range(n_samples):
            point_list = []
            for b in bounds:
                point_list.append(np.random.uniform(b[0], b[1]))
            point = np.array(point_list).reshape(1, -1)

            mu, sigma = self.gp_model.predict(point, return_std=True)
            sample = np.random.normal(mu, sigma)
            samples.append(float(sample[0]))

        return np.array(samples)
    
    async def run_pymc_optimization(
        self,
        model_builder: Callable[..., Any],
        data: dict[str, Any],
        n_samples: int = 2000,
        n_chains: int = 4,
    ) -> dict[str, Any]:
        """
        Run Bayesian optimization using PyMC for complex statistical models.
        
        Args:
            model_builder: Function that builds PyMC model
            data: Data for the model
            n_samples: Number of MCMC samples
            n_chains: Number of MCMC chains
            
        Returns:
            Dictionary with posterior samples and diagnostics
        """
        # Build PyMC model
        with model_builder(data, self.parameter_priors) as model:
            # Sample from posterior
            trace = pm.sample(
                n_samples,
                chains=n_chains,
                return_inferencedata=True,
                progressbar=True
            )
            
            # Get posterior predictive samples
            posterior_predictive = pm.sample_posterior_predictive(
                trace,
                progressbar=True
            )
        
        # Extract results
        results = {
            "trace": trace,
            "posterior_predictive": posterior_predictive,
            "summary": az.summary(trace),
            "diagnostics": {
                "rhat": az.rhat(trace),
                "ess": az.ess(trace),
                "mcse": az.mcse(trace)
            }
        }
        
        # Find best parameters (MAP estimate)
        posterior_means = {}
        for param in self.parameter_priors.keys():
            if param in trace.posterior:
                posterior_means[param] = float(
                    trace.posterior[param].mean().values
                )
        
        results["best_params"] = posterior_means
        
        return results
    
    def update_posterior(
        self, new_observations: list[tuple[dict[str, float], float]]
    ) -> None:
        """
        Update posterior with new observations.
        
        Args:
            new_observations: List of (parameters, value) tuples
        """
        for params, value in new_observations:
            # Convert dict to array
            point_list = []
            for name in self.parameter_priors.keys():
                point_list.append(params[name])

            self.X_observed.append(np.array(point_list))
            self.y_observed.append(value)
        
        # Refit GP model
        if len(self.X_observed) > 0:
            X = np.array(self.X_observed)
            y = np.array(self.y_observed)
            self.gp_model.fit(X, y)
    
    def get_uncertainty_regions(
        self, confidence_level: float = 0.95
    ) -> dict[str, tuple[float, float]]:
        """
        Get uncertainty regions for parameters.
        
        Args:
            confidence_level: Confidence level for intervals
            
        Returns:
            Dictionary mapping parameter names to confidence intervals
        """
        if len(self.X_observed) == 0:
            # Return prior bounds if no observations
            return {name: prior.bounds 
                   for name, prior in self.parameter_priors.items()}
        
        # Compute confidence intervals from GP predictions
        intervals = {}
        for i, name in enumerate(self.parameter_priors.keys()):
            values = [x[i] for x in self.X_observed]
            
            # Use GP to predict over parameter range
            param_range = np.linspace(
                self.parameter_priors[name].bounds[0],
                self.parameter_priors[name].bounds[1],
                100
            )
            
            # Create test points
            test_points_list: list[np.ndarray] = []
            for val in param_range:
                point = self.X_observed[-1].copy()  # Use last observation as base
                point[i] = val
                test_points_list.append(point)

            test_points = np.array(test_points_list)
            mu, sigma = self.gp_model.predict(test_points, return_std=True)
            
            # Compute confidence interval
            z_score = norm.ppf((1 + confidence_level) / 2)
            lower = mu - z_score * sigma
            upper = mu + z_score * sigma
            
            # Find parameter values at confidence bounds
            best_idx = np.argmax(mu)
            confidence_range = param_range[
                (lower >= lower[best_idx] - sigma[best_idx])
            ]
            
            if len(confidence_range) > 0:
                intervals[name] = (confidence_range.min(), confidence_range.max())
            else:
                intervals[name] = self.parameter_priors[name].bounds
        
        return intervals