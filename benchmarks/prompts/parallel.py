"""
HPC-specific prompts: MPI, OpenMP, CUDA, parallel programming.
"""

PARALLEL_PROMPTS = [
    {
        "id": "par_01_mpi_sum",
        "category": "parallel",
        "language": "c",
        "subcategory": "mpi",
        "prompt": (
            "Write a C program using MPI that computes the sum of an array of integers "
            "distributed across ranks. Rank 0 initializes an array of size N, scatters it "
            "to all processes using MPI_Scatter, each process computes its partial sum, "
            "then use MPI_Reduce to get the total sum back on rank 0. "
            "N should be 100. Print the total sum from rank 0.\n\n"
            "The program must compile with: mpicc -o prog prog.c\n"
            "And run with: mpirun -np 4 ./prog\n"
            "Provide only the complete C code."
        ),
        "expected_output": "Total sum: 4950",
        "compiler": "mpicc",
        "run_cmd": "mpirun -np 4 --oversubscribe ./{binary}",
        "timeout_sec": 10,
    },
    {
        "id": "par_02_openmp_loop",
        "category": "parallel",
        "language": "c",
        "subcategory": "openmp",
        "prompt": (
            "Write a C program that parallelizes a vector addition loop using OpenMP. "
            "Create two arrays A and B of size 1000000, fill A[i]=i, B[i]=i*2, "
            "then compute C[i] = A[i] + B[i] in parallel. "
            "Use OpenMP's parallel for directive with appropriate scheduling. "
            "After computation, verify the first 10 and last 10 elements are correct by printing them.\n\n"
            "Compile with: gcc -fopenmp -O2 -o prog prog.c\n"
            "Run with: OMP_NUM_THREADS=4 ./prog\n"
            "Provide only the complete C code."
        ),
        "expected_output": "C[0]=0 C[1]=3 C[2]=6 C[8]=24 C[9]=27 ... C[999990]=2999970 C[999999]=2999997",
        "compiler": "gcc",
        "compiler_flags": "-fopenmp -O2",
        "run_env": {"OMP_NUM_THREADS": "4"},
        "timeout_sec": 15,
    },
    {
        "id": "par_03_cuda_saxpy",
        "category": "parallel",
        "language": "c",
        "subcategory": "cuda",
        "prompt": (
            "Write a CUDA kernel for the SAXPY operation: y[i] = a * x[i] + y[i] "
            "for vectors of size N. The host code should allocate memory, "
            "initialize x with values 1..N and y with values N..1, "
            "copy to device, launch the kernel with appropriate grid/block sizing, "
            "copy back, and verify the first 5 results by printing them. "
            "Use N=1024, a=2.0f.\n\n"
            "Compile with: nvcc -o saxpy saxpy.cu\n"
            "Run with: ./saxpy\n"
            "Provide only the complete CUDA code."
        ),
        "expected_output": "y[0]=2048.000000 y[1]=2045.000000 y[2]=2042.000000 y[3]=2039.000000 y[4]=2036.000000",
        "compiler": "nvcc",
        "timeout_sec": 15,
        "skip_if_no_gpu": True,
    },
    {
        "id": "par_04_mpi_pi",
        "category": "parallel",
        "language": "c",
        "subcategory": "mpi",
        "prompt": (
            "Write a C program using MPI that computes an approximation of π using the "
            "numerical integration formula:\n"
            "  π = ∫₀¹ 4/(1+x²) dx\n\n"
            "Use the rectangle method with N=1000000 intervals. "
            "Distribute intervals evenly across MPI ranks, each rank computes its partial sum, "
            "then use MPI_Reduce to get the total. Rank 0 prints the final π approximation.\n\n"
            "The result should be close to 3.141593.\n"
            "Compile with: mpicc -o pi pi.c\n"
            "Run with: mpirun -np 4 ./pi\n"
            "Provide only the complete C code."
        ),
        "expected_output_pattern": r"3\.1415[89]\d*",
        "compiler": "mpicc",
        "run_cmd": "mpirun -np 4 --oversubscribe ./{binary}",
        "timeout_sec": 10,
    },
    {
        "id": "par_05_openmp_dot_product",
        "category": "parallel",
        "language": "c",
        "subcategory": "openmp",
        "prompt": (
            "Write a C program that computes the dot product of two vectors of size "
            "N=1000000 using OpenMP. Fill A[i]=i, B[i]=N-i. "
            "Use reduction to safely accumulate the dot product in parallel. "
            "Verify by printing the result (it should be computable from formula).\n\n"
            "Compile with: gcc -fopenmp -O2 -o dot dot.c\n"
            "Run with: OMP_NUM_THREADS=4 ./dot\n"
            "Provide only the complete C code."
        ),
        "expected_output_pattern": r".*",
        "compiler": "gcc",
        "compiler_flags": "-fopenmp -O2",
        "run_env": {"OMP_NUM_THREADS": "4"},
        "timeout_sec": 15,
    },
]
