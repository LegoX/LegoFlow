#include <iostream>
#include <omp.h>
#include "worker.h"

int main() {
    std::cout << "Starting OpenMP parallel example..." << std::endl;
    std::cout << "Number of available threads: " << omp_get_max_threads() << std::endl;
    std::cout << "\nExecuting parallel work:\n" << std::endl;
    
    parallel_work(10);
    
    std::cout << "\nCompleted successfully!" << std::endl;
    return 0;
}
