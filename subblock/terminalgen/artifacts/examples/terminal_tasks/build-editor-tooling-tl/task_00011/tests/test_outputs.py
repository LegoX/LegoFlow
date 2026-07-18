import os
import subprocess
import pytest


def test_makefile_fixed_with_fopenmp():
    """Test that Makefile has been fixed with LDFLAGS = -fopenmp"""
    makefile_path = "/app/task_file/Makefile"
    assert os.path.exists(makefile_path), "Makefile should exist"
    
    with open(makefile_path, 'r') as f:
        content = f.read()
    
    # The fix adds LDFLAGS = -fopenmp
    assert "LDFLAGS = -fopenmp" in content, \
        "Makefile should have LDFLAGS = -fopenmp for OpenMP linking"


def test_binary_exists():
    """Test that the compiled binary exists at output/worker_app"""
    binary_path = "/app/task_file/output/worker_app"
    assert os.path.exists(binary_path), \
        f"Binary should be created at {binary_path}"
    assert os.path.isfile(binary_path), \
        f"{binary_path} should be a regular file"


def test_binary_is_executable():
    """Test that the binary has executable permissions"""
    binary_path = "/app/task_file/output/worker_app"
    assert os.access(binary_path, os.X_OK), \
        "Binary should have executable permissions"


def test_binary_executes_successfully():
    """Test that the binary runs without errors"""
    binary_path = "/app/task_file/output/worker_app"
    result = subprocess.run(
        [binary_path],
        capture_output=True,
        text=True,
        timeout=10
    )
    
    assert result.returncode == 0, \
        f"Binary should exit with code 0, got {result.returncode}.\nstderr: {result.stderr}"


def test_binary_output_shows_openmp():
    """Test that binary output indicates parallel processing with OpenMP"""
    binary_path = "/app/task_file/output/worker_app"
    result = subprocess.run(
        [binary_path],
        capture_output=True,
        text=True,
        timeout=10
    )
    
    combined_output = (result.stdout + result.stderr).lower()
    
    # Look for evidence of OpenMP in output
    openmp_keywords = ["openmp", "thread", "parallel", "available"]
    assert any(kw in combined_output for kw in openmp_keywords), \
        f"Output should show OpenMP activity. Got stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"


def test_build_directory_has_object_files():
    """Test that object files were successfully compiled"""
    build_dir = "/app/task_file/build"
    assert os.path.isdir(build_dir), \
        "Build directory should exist with compiled object files"
    
    obj_files = [f for f in os.listdir(build_dir) if f.endswith('.o')]
    assert len(obj_files) > 0, \
        "Build directory should contain at least one .o object file"
